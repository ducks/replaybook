use crate::scenario::{BreakStep, Scenario, SuccessCondition};
use anyhow::Result;
use std::process::Command;

pub struct Issue {
    pub message: String,
}

pub fn validate(scenario: &Scenario) -> Result<Vec<Issue>> {
    let mut issues = Vec::new();

    let compose_file = scenario.compose_file();
    if !compose_file.exists() {
        issues.push(Issue {
            message: format!("missing {}", compose_file.display()),
        });
        // Nothing else below can be checked without a compose file.
        return Ok(issues);
    }

    let config = Command::new("docker")
        .args(["compose", "-f"])
        .arg(&compose_file)
        .args(["config", "--services"])
        .output()?;

    if !config.status.success() {
        issues.push(Issue {
            message: format!(
                "docker-compose.yml is invalid: {}",
                String::from_utf8_lossy(&config.stderr).trim()
            ),
        });
        return Ok(issues);
    }

    let services: Vec<&str> = std::str::from_utf8(&config.stdout)?
        .lines()
        .filter(|l| !l.is_empty())
        .collect();

    if services.is_empty() {
        issues.push(Issue {
            message: "docker-compose.yml defines no services".to_string(),
        });
    }

    if let Some(shell_service) = scenario.meta.shell_service.as_deref() {
        if !services.contains(&shell_service) {
            issues.push(Issue {
                message: format!(
                    "shell_service \"{shell_service}\" is not a service in docker-compose.yml (found: {})",
                    services.join(", ")
                ),
            });
        }
    }

    match &scenario.meta.break_steps {
        Some(steps) => {
            for (i, step) in steps.iter().enumerate() {
                let service = match step {
                    BreakStep::Cp { service, src, .. } => {
                        if !scenario.dir.join(src).exists() {
                            issues.push(Issue {
                                message: format!("break[{i}]: cp source \"{src}\" does not exist"),
                            });
                        }
                        service
                    }
                    BreakStep::Exec { service, .. } => service,
                    BreakStep::Restart { service } => service,
                };
                if !services.contains(&service.as_str()) {
                    issues.push(Issue {
                        message: format!(
                            "break[{i}]: service \"{service}\" is not a service in docker-compose.yml (found: {})",
                            services.join(", ")
                        ),
                    });
                }
            }
        }
        None => {
            if !scenario.break_script().exists() {
                issues.push(Issue {
                    message: "missing break.sh (or a break: [...] step list in meta.json)"
                        .to_string(),
                });
            }
        }
    }

    match scenario.meta.success_condition {
        SuccessCondition::ExitZero => {
            if !scenario.check_script().exists() {
                issues.push(Issue {
                    message: "success_condition is exit_zero but check.sh is missing".to_string(),
                });
            }
        }
        SuccessCondition::Http200 => {
            if scenario.meta.success_target.trim().is_empty() {
                issues.push(Issue {
                    message: "success_condition is http_200 but success_target is empty"
                        .to_string(),
                });
            } else if !scenario.meta.success_target.starts_with("http://")
                && !scenario.meta.success_target.starts_with("https://")
            {
                issues.push(Issue {
                    message: format!(
                        "success_target \"{}\" is not a valid http(s) URL",
                        scenario.meta.success_target
                    ),
                });
            }
        }
    }

    Ok(issues)
}
