use crate::scenario::{IncidentSource, ScenarioMeta, SuccessCondition};
use anyhow::{Context, Result, bail};
use std::fs;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

pub fn create(
    id: &str,
    pack_dir: &Path,
    input: &mut impl BufRead,
    output: &mut impl Write,
) -> Result<PathBuf> {
    validate_id(id)?;
    let scenario_dir = pack_dir.join(id);
    if scenario_dir.exists() {
        bail!("{} already exists", scenario_dir.display());
    }

    writeln!(
        output,
        "Create a runnable starter scenario. Press Enter to accept defaults.\n"
    )?;
    let title = prompt(input, output, "Title", &title_from_id(id))?;
    let page = prompt(
        input,
        output,
        "Initial page",
        "TODO: describe the user-visible incident symptoms",
    )?;
    let difficulty = prompt_difficulty(input, output)?;
    let tags = split_list(&prompt(input, output, "Tags (comma-separated)", "")?, ',');
    let hints = split_list(
        &prompt(input, output, "Hints (semicolon-separated)", "")?,
        ';',
    );
    let learning_objectives = split_list(
        &prompt(
            input,
            output,
            "Learning objectives (semicolon-separated)",
            "",
        )?,
        ';',
    );
    let service = prompt(input, output, "Primary service", "app")?;
    validate_service(&service)?;
    let break_command = prompt(
        input,
        output,
        "Fault command inside service",
        "rm -f /srv/www/health",
    )?;
    let solve_command = prompt(
        input,
        output,
        "Repair command inside service",
        "echo ok > /srv/www/health",
    )?;
    let success_condition = prompt_condition(input, output)?;
    let success_target = match success_condition {
        SuccessCondition::Http200 => {
            prompt(input, output, "Health URL", "http://localhost:8080/health")?
        }
        SuccessCondition::ExitZero => String::new(),
    };
    let incident_date = optional(prompt(input, output, "Incident date (YYYY-MM-DD)", "")?);
    let reference = optional(prompt(input, output, "Incident reference", "")?);
    let source = if incident_date.is_some() || reference.is_some() {
        let sanitized = prompt_bool(input, output, "Sanitized", true)?;
        Some(IncidentSource {
            incident_date,
            reference,
            sanitized,
        })
    } else {
        None
    };

    let meta = ScenarioMeta {
        id: id.to_owned(),
        title,
        page,
        difficulty,
        tags,
        hints,
        success_condition: success_condition.clone(),
        success_target,
        break_steps: None,
        faults: Vec::new(),
        source,
        learning_objectives,
    };

    fs::create_dir_all(&scenario_dir)
        .with_context(|| format!("creating {}", scenario_dir.display()))?;
    let result = write_files(
        &scenario_dir,
        &service,
        &break_command,
        &solve_command,
        &meta,
    );
    if result.is_err() {
        fs::remove_dir_all(&scenario_dir).ok();
    }
    result?;

    writeln!(output, "\nCreated {}", scenario_dir.display())?;
    writeln!(output, "  replaybook validate {}", scenario_dir.display())?;
    writeln!(output, "  replaybook test {}", scenario_dir.display())?;
    writeln!(output, "  replaybook run {}", scenario_dir.display())?;
    Ok(scenario_dir)
}

fn write_files(
    dir: &Path,
    service: &str,
    break_command: &str,
    solve_command: &str,
    meta: &ScenarioMeta,
) -> Result<()> {
    let meta_json = serde_json::to_string_pretty(meta)? + "\n";
    fs::write(dir.join("meta.json"), meta_json)?;
    fs::write(
        dir.join("docker-compose.yml"),
        format!(
            "services:\n  {service}:\n    image: python:3.12-alpine\n    command: sh -c \"mkdir -p /srv/www && echo ok > /srv/www/health && cd /srv/www && python -m http.server 3000\"\n    ports:\n      - \"8080:3000\"\n"
        ),
    )?;

    let compose = r#"SCENARIO_DIR="$(cd "$(dirname "$0")" && pwd)"
docker compose -f "$SCENARIO_DIR/docker-compose.yml""#;
    let break_script = format!(
        "#!/usr/bin/env bash\nset -euo pipefail\n\n{compose} exec -T {service} sh -c {}\n",
        shell_quote(break_command)
    );
    let solve_script = format!(
        "#!/usr/bin/env bash\nset -euo pipefail\n\n{compose} exec -T {service} sh -c {}\n",
        shell_quote(solve_command)
    );
    fs::write(dir.join("break.sh"), break_script)?;
    fs::write(dir.join("solve.sh"), solve_script)?;

    if matches!(meta.success_condition, SuccessCondition::ExitZero) {
        let check_script = format!(
            "#!/usr/bin/env bash\nset -euo pipefail\n\n{compose} exec -T {service} test -f /srv/www/health\n"
        );
        fs::write(dir.join("check.sh"), check_script)?;
    }

    #[cfg(unix)]
    for name in ["break.sh", "solve.sh", "check.sh"] {
        let path = dir.join(name);
        if path.exists() {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(0o755))?;
        }
    }
    Ok(())
}

fn prompt(
    input: &mut impl BufRead,
    output: &mut impl Write,
    label: &str,
    default: &str,
) -> Result<String> {
    if default.is_empty() {
        write!(output, "{label}: ")?;
    } else {
        write!(output, "{label} [{default}]: ")?;
    }
    output.flush()?;
    let mut line = String::new();
    input.read_line(&mut line)?;
    let value = line.trim();
    Ok(if value.is_empty() {
        default.to_owned()
    } else {
        value.to_owned()
    })
}

fn prompt_difficulty(input: &mut impl BufRead, output: &mut impl Write) -> Result<u8> {
    loop {
        let value = prompt(input, output, "Difficulty (1-5)", "1")?;
        match value.parse::<u8>() {
            Ok(value @ 1..=5) => return Ok(value),
            _ => writeln!(output, "  Enter a number from 1 to 5.")?,
        }
    }
}

fn prompt_condition(input: &mut impl BufRead, output: &mut impl Write) -> Result<SuccessCondition> {
    loop {
        let value = prompt(
            input,
            output,
            "Success check (http_200 or exit_zero)",
            "http_200",
        )?;
        match value.as_str() {
            "http_200" => return Ok(SuccessCondition::Http200),
            "exit_zero" => return Ok(SuccessCondition::ExitZero),
            _ => writeln!(output, "  Enter http_200 or exit_zero.")?,
        }
    }
}

fn prompt_bool(
    input: &mut impl BufRead,
    output: &mut impl Write,
    label: &str,
    default: bool,
) -> Result<bool> {
    loop {
        let default_text = if default { "yes" } else { "no" };
        match prompt(input, output, label, default_text)?
            .to_lowercase()
            .as_str()
        {
            "y" | "yes" => return Ok(true),
            "n" | "no" => return Ok(false),
            _ => writeln!(output, "  Enter yes or no.")?,
        }
    }
}

fn split_list(value: &str, separator: char) -> Vec<String> {
    value
        .split(separator)
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(String::from)
        .collect()
}

fn optional(value: String) -> Option<String> {
    (!value.is_empty()).then_some(value)
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn validate_id(id: &str) -> Result<()> {
    let valid = !id.is_empty()
        && id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !id.starts_with('-')
        && !id.ends_with('-');
    if !valid {
        bail!("scenario ID must contain lowercase letters, digits, and single hyphens");
    }
    Ok(())
}

fn validate_service(service: &str) -> Result<()> {
    if service.is_empty()
        || !service
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        bail!("service name must contain only letters, digits, hyphens, and underscores");
    }
    Ok(())
}

fn title_from_id(id: &str) -> String {
    let title = id
        .split('-')
        .filter(|part| !part.chars().all(|c| c.is_ascii_digit()))
        .map(|part| {
            let mut chars = part.chars();
            match chars.next() {
                Some(first) => first.to_ascii_uppercase().to_string() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    if title.is_empty() {
        id.to_owned()
    } else {
        title
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scenario::Scenario;
    use std::io::Cursor;

    #[test]
    fn creates_a_runnable_http_scenario() {
        let root = tempfile::tempdir().unwrap();
        let answers = "Checkout Is Down\ncustomers see 500s\n3\npostgres,database\ncheck logs;inspect connections\nrecognize exhaustion\nweb\nrm -f /srv/www/health\necho it's-fixed > /srv/www/health\nhttp_200\nhttp://localhost:8080/health\n2026-06-14\nINC-1842\nyes\n";
        let path = create(
            "010-checkout-down",
            root.path(),
            &mut Cursor::new(answers),
            &mut Vec::new(),
        )
        .unwrap();
        let scenario = Scenario::load(&path).unwrap();
        assert_eq!(scenario.meta.difficulty, 3);
        assert_eq!(scenario.meta.tags, ["postgres", "database"]);
        assert_eq!(scenario.meta.hints.len(), 2);
        assert_eq!(scenario.meta.learning_objectives, ["recognize exhaustion"]);
        assert_eq!(
            scenario.meta.source.unwrap().reference.as_deref(),
            Some("INC-1842")
        );
        assert!(path.join("break.sh").exists());
        assert!(path.join("solve.sh").exists());
        assert!(!path.join("check.sh").exists());
        assert!(
            fs::read_to_string(path.join("docker-compose.yml"))
                .unwrap()
                .contains("  web:")
        );
        assert!(
            fs::read_to_string(path.join("solve.sh"))
                .unwrap()
                .contains("'echo it'\"'\"'s-fixed > /srv/www/health'")
        );
    }

    #[test]
    fn defaults_create_an_exit_zero_check_when_selected() {
        let root = tempfile::tempdir().unwrap();
        let answers = "\n\n\n\n\n\n\n\n\nexit_zero\n\n\n";
        let path = create(
            "001-disk-full",
            root.path(),
            &mut Cursor::new(answers),
            &mut Vec::new(),
        )
        .unwrap();
        let scenario = Scenario::load(&path).unwrap();
        assert!(matches!(
            scenario.meta.success_condition,
            SuccessCondition::ExitZero
        ));
        assert!(path.join("check.sh").exists());
        assert_eq!(scenario.meta.title, "Disk Full");
        assert!(scenario.meta.source.is_none());
    }

    #[test]
    fn rejects_unsafe_ids_and_existing_destinations() {
        let root = tempfile::tempdir().unwrap();
        assert!(
            create(
                "../oops",
                root.path(),
                &mut Cursor::new(""),
                &mut Vec::new()
            )
            .is_err()
        );
        fs::create_dir(root.path().join("exists")).unwrap();
        assert!(create("exists", root.path(), &mut Cursor::new(""), &mut Vec::new()).is_err());
    }
}
