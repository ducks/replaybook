use crate::scenario::{BreakStep, Scenario, SuccessCondition};
use anyhow::Result;
use std::process::Command;

pub struct Issue {
    pub message: String,
}

fn issue(message: impl Into<String>) -> Issue {
    Issue {
        message: message.into(),
    }
}

pub fn validate(scenario: &Scenario) -> Result<Vec<Issue>> {
    let compose_file = scenario.compose_file();
    if !compose_file.exists() {
        // Nothing else below can be checked without a compose file.
        return Ok(vec![issue(format!("missing {}", compose_file.display()))]);
    }

    let config = Command::new("docker")
        .args(["compose", "-f"])
        .arg(&compose_file)
        .args(["config", "--services"])
        .output()?;

    if !config.status.success() {
        return Ok(vec![issue(format!(
            "docker-compose.yml is invalid: {}",
            String::from_utf8_lossy(&config.stderr).trim()
        ))]);
    }

    let services: Vec<&str> = std::str::from_utf8(&config.stdout)?
        .lines()
        .filter(|l| !l.is_empty())
        .collect();

    Ok(validate_with_services(scenario, &services))
}

/// The docker-free core: every rule that can be checked once the compose
/// file's service list is known.
fn validate_with_services(scenario: &Scenario, services: &[&str]) -> Vec<Issue> {
    let mut issues = Vec::new();

    if services.is_empty() {
        issues.push(issue("docker-compose.yml defines no services"));
    }

    if scenario.meta.faults.is_empty() {
        match &scenario.meta.break_steps {
            Some(steps) => validate_steps(scenario, steps, services, "break", &mut issues),
            None => {
                if !scenario.break_script().exists() {
                    issues.push(issue(
                        "missing break.sh (or a break: [...] step list, or a faults: [...] list in meta.json)",
                    ));
                }
            }
        }
    } else {
        if scenario.meta.break_steps.is_some() {
            issues.push(issue(
                "both a top-level break: [...] and a faults: [...] list are defined - faults win, drop the top-level break",
            ));
        }
        let mut seen = std::collections::HashSet::new();
        for fault in &scenario.meta.faults {
            if fault.name.trim().is_empty() {
                issues.push(issue("faults[]: fault with an empty name"));
                continue;
            }
            if !seen.insert(fault.name.as_str()) {
                issues.push(issue(format!(
                    "faults[]: duplicate name \"{}\"",
                    fault.name
                )));
            }
            let label = format!("faults[{}].break", fault.name);
            match &fault.break_steps {
                Some(steps) => validate_steps(scenario, steps, services, &label, &mut issues),
                None => match &fault.script {
                    Some(script) => {
                        if !scenario.dir.join(script).exists() {
                            issues.push(issue(format!(
                                "faults[{}]: script \"{script}\" does not exist",
                                fault.name
                            )));
                        }
                    }
                    None => issues.push(issue(format!(
                        "faults[{}]: needs a break: [...] list or a script",
                        fault.name
                    ))),
                },
            }
            if let Some(solve) = &fault.solve {
                if !scenario.dir.join(solve).exists() {
                    issues.push(issue(format!(
                        "faults[{}]: solve script \"{solve}\" does not exist",
                        fault.name
                    )));
                }
            }
        }
    }

    match scenario.meta.success_condition {
        SuccessCondition::ExitZero => {
            if !scenario.check_script().exists() {
                issues.push(issue(
                    "success_condition is exit_zero but check.sh is missing",
                ));
            }
        }
        SuccessCondition::Http200 => {
            if scenario.meta.success_target.trim().is_empty() {
                issues.push(issue(
                    "success_condition is http_200 but success_target is empty",
                ));
            } else if !scenario.meta.success_target.starts_with("http://")
                && !scenario.meta.success_target.starts_with("https://")
            {
                issues.push(issue(format!(
                    "success_target \"{}\" is not a valid http(s) URL",
                    scenario.meta.success_target
                )));
            }
        }
    }

    issues
}

fn validate_steps(
    scenario: &Scenario,
    steps: &[BreakStep],
    services: &[&str],
    label: &str,
    issues: &mut Vec<Issue>,
) {
    for (i, step) in steps.iter().enumerate() {
        let service = match step {
            BreakStep::Cp { service, src, .. } => {
                if !scenario.dir.join(src).exists() {
                    issues.push(issue(format!(
                        "{label}[{i}]: cp source \"{src}\" does not exist"
                    )));
                }
                service
            }
            BreakStep::Exec { service, .. } => service,
            BreakStep::Restart { service } => service,
        };
        if !services.contains(&service.as_str()) {
            issues.push(issue(format!(
                "{label}[{i}]: service \"{service}\" is not a service in docker-compose.yml (found: {})",
                services.join(", ")
            )));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scenario::ScenarioMeta;
    use std::path::Path;

    fn scenario_in(dir: &Path, meta_json: &str) -> Scenario {
        let meta: ScenarioMeta = serde_json::from_str(meta_json).unwrap();
        Scenario {
            meta,
            dir: dir.to_path_buf(),
        }
    }

    fn meta(success: &str, target: &str, break_json: &str) -> String {
        format!(
            r#"{{
                "id": "x", "title": "t", "page": "p", "difficulty": 1,
                "hints": [], "success_condition": "{success}",
                "success_target": "{target}"{break_json}
            }}"#
        )
    }

    fn messages(issues: &[Issue]) -> Vec<&str> {
        issues.iter().map(|i| i.message.as_str()).collect()
    }

    #[test]
    fn valid_scenario_with_break_script_passes() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("break.sh"), "#!/bin/bash\n").unwrap();
        let s = scenario_in(dir.path(), &meta("http_200", "http://localhost:8080/", ""));
        assert!(validate_with_services(&s, &["app"]).is_empty());
    }

    #[test]
    fn empty_service_list_is_an_issue() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("break.sh"), "").unwrap();
        let s = scenario_in(dir.path(), &meta("http_200", "http://x/", ""));
        let issues = validate_with_services(&s, &[]);
        assert_eq!(
            messages(&issues),
            vec!["docker-compose.yml defines no services"]
        );
    }

    #[test]
    fn missing_break_sh_and_no_break_steps_is_an_issue() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(dir.path(), &meta("http_200", "http://x/", ""));
        let issues = validate_with_services(&s, &["app"]);
        assert_eq!(
            messages(&issues),
            vec![
                "missing break.sh (or a break: [...] step list, or a faults: [...] list in meta.json)"
            ]
        );
    }

    #[test]
    fn break_step_service_must_exist_in_compose() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(
            dir.path(),
            &meta(
                "http_200",
                "http://x/",
                r#", "break": [ { "restart": { "service": "db" } } ]"#,
            ),
        );
        let issues = validate_with_services(&s, &["app", "nginx"]);
        assert_eq!(
            messages(&issues),
            vec![
                "break[0]: service \"db\" is not a service in docker-compose.yml (found: app, nginx)"
            ]
        );
    }

    #[test]
    fn cp_step_source_must_exist_in_scenario_dir() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(
            dir.path(),
            &meta(
                "http_200",
                "http://x/",
                r#", "break": [ { "cp": { "service": "app", "src": "broken.conf", "dest": "/etc/x" } } ]"#,
            ),
        );
        let issues = validate_with_services(&s, &["app"]);
        assert_eq!(
            messages(&issues),
            vec!["break[0]: cp source \"broken.conf\" does not exist"]
        );

        // And it clears once the file exists.
        std::fs::write(dir.path().join("broken.conf"), "x").unwrap();
        assert!(validate_with_services(&s, &["app"]).is_empty());
    }

    #[test]
    fn exit_zero_requires_check_sh() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("break.sh"), "").unwrap();
        let s = scenario_in(dir.path(), &meta("exit_zero", "", ""));
        let issues = validate_with_services(&s, &["app"]);
        assert_eq!(
            messages(&issues),
            vec!["success_condition is exit_zero but check.sh is missing"]
        );

        std::fs::write(dir.path().join("check.sh"), "").unwrap();
        assert!(validate_with_services(&s, &["app"]).is_empty());
    }

    #[test]
    fn http_200_requires_an_http_target() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("break.sh"), "").unwrap();

        let empty = scenario_in(dir.path(), &meta("http_200", "", ""));
        assert_eq!(
            messages(&validate_with_services(&empty, &["app"])),
            vec!["success_condition is http_200 but success_target is empty"]
        );

        let bogus = scenario_in(dir.path(), &meta("http_200", "localhost:8080", ""));
        assert_eq!(
            messages(&validate_with_services(&bogus, &["app"])),
            vec!["success_target \"localhost:8080\" is not a valid http(s) URL"]
        );

        let https = scenario_in(dir.path(), &meta("http_200", "https://x/health", ""));
        assert!(validate_with_services(&https, &["app"]).is_empty());
    }

    #[test]
    fn multiple_issues_accumulate() {
        let dir = tempfile::tempdir().unwrap();
        // No break.sh, exit_zero without check.sh: two independent issues.
        let s = scenario_in(dir.path(), &meta("exit_zero", "", ""));
        let issues = validate_with_services(&s, &["app"]);
        assert_eq!(issues.len(), 2);
    }

    #[test]
    fn faults_conflict_with_top_level_break() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(
            dir.path(),
            &meta(
                "http_200",
                "http://x/",
                r#", "break": [ { "restart": { "service": "app" } } ],
                   "faults": [ { "name": "a", "break": [ { "restart": { "service": "app" } } ] } ]"#,
            ),
        );
        let issues = validate_with_services(&s, &["app"]);
        assert_eq!(issues.len(), 1);
        assert!(issues[0].message.contains("faults win"));
    }

    #[test]
    fn fault_names_must_be_unique_and_nonempty() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(
            dir.path(),
            &meta(
                "http_200",
                "http://x/",
                r#", "faults": [
                    { "name": "a", "break": [ { "restart": { "service": "app" } } ] },
                    { "name": "a", "break": [ { "restart": { "service": "app" } } ] },
                    { "name": " ", "break": [ { "restart": { "service": "app" } } ] }
                ]"#,
            ),
        );
        let issues = validate_with_services(&s, &["app"]);
        let msgs = messages(&issues);
        assert!(msgs.contains(&"faults[]: duplicate name \"a\""));
        assert!(msgs.contains(&"faults[]: fault with an empty name"));
    }

    #[test]
    fn fault_needs_steps_or_existing_script() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(
            dir.path(),
            &meta(
                "http_200",
                "http://x/",
                r#", "faults": [
                    { "name": "no-injection" },
                    { "name": "missing-script", "script": "break-x.sh" }
                ]"#,
            ),
        );
        let issues = validate_with_services(&s, &["app"]);
        let msgs = messages(&issues);
        assert!(msgs.contains(&"faults[no-injection]: needs a break: [...] list or a script"));
        assert!(msgs.contains(&"faults[missing-script]: script \"break-x.sh\" does not exist"));

        std::fs::write(dir.path().join("break-x.sh"), "").unwrap();
        let msgs2 = validate_with_services(&s, &["app"]);
        assert_eq!(msgs2.len(), 1); // only the no-injection fault remains
    }

    #[test]
    fn fault_break_steps_validate_services_with_fault_label() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(
            dir.path(),
            &meta(
                "http_200",
                "http://x/",
                r#", "faults": [
                    { "name": "bad", "break": [ { "restart": { "service": "ghost" } } ],
                      "solve": "solve-bad.sh" }
                ]"#,
            ),
        );
        let issues = validate_with_services(&s, &["app"]);
        let msgs = messages(&issues);
        assert!(
            msgs.iter()
                .any(|m| m.starts_with("faults[bad].break[0]: service \"ghost\""))
        );
        assert!(msgs.contains(&"faults[bad]: solve script \"solve-bad.sh\" does not exist"));
    }

    #[test]
    fn reports_missing_compose_file_without_invoking_docker() {
        let dir = tempfile::tempdir().unwrap();
        let s = scenario_in(dir.path(), &meta("http_200", "http://x/", ""));

        let issues = validate(&s).expect("validation should succeed");

        assert_eq!(issues.len(), 1);
        assert!(issues[0].message.contains("missing"));
        assert!(issues[0].message.contains("docker-compose.yml"));
    }
}
