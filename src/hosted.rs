use crate::backend::{ExecutionBackend, HostedPhase, HostedStatus, LocalDockerBackend};
use crate::recorder::{self, Outcome};
use crate::scenario::Scenario;
use crate::validate;
use anyhow::{Context, Result, bail};
use chrono::Utc;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use uuid::Uuid;

pub async fn run(
    session_id: &str,
    scenario_path: &Path,
    sla_minutes: u64,
    fault: Option<&str>,
) -> Result<()> {
    let id = parse_id(session_id)?;
    require_session_scenario(id, scenario_path)?;
    if sla_minutes == 0 {
        bail!("SLA must be greater than zero");
    }
    let _guard = HostedRunGuard::start(id)?;
    write_status(HostedStatus {
        session_id: id,
        phase: HostedPhase::Active,
        updated_at: Utc::now(),
        elapsed_secs: None,
        hints_used: Some(0),
        message: None,
    })?;
    let scenario = Scenario::load(scenario_path)?;
    let issues = validate::validate(&scenario)?;
    if !issues.is_empty() {
        let detail = issues
            .iter()
            .map(|issue| issue.message.as_str())
            .collect::<Vec<_>>()
            .join("; ");
        bail!("hosted scenario failed validation: {detail}");
    }
    let active_fault = scenario.select_fault(fault, entropy())?;

    // Every hosted session gets its own Compose project. This is defense in
    // depth; the supported MVP still dedicates the entire VM to one trainee.
    unsafe {
        std::env::set_var(
            "COMPOSE_PROJECT_NAME",
            format!("replaybook_{}", id.simple()),
        );
    }

    let result = LocalDockerBackend
        .run(&scenario, &active_fault, sla_minutes * 60)
        .await;
    let fault_name = active_fault.name.as_deref();
    match result {
        Ok(crate::runner::RunResult::Success {
            elapsed,
            hints_used,
            transcript,
        }) => {
            recorder::record(
                &scenario.meta.id,
                Outcome::Success,
                Some(elapsed),
                hints_used as u8,
                transcript.as_deref(),
                fault_name,
            )?;
            write_status(HostedStatus {
                session_id: id,
                phase: HostedPhase::Succeeded,
                updated_at: Utc::now(),
                elapsed_secs: Some(elapsed.as_secs()),
                hints_used: Some(hints_used),
                message: None,
            })?;
            println!("\n✓ hosted incident resolved in {}s", elapsed.as_secs());
        }
        Ok(crate::runner::RunResult::Timeout {
            hints_used,
            transcript,
        }) => {
            recorder::record(
                &scenario.meta.id,
                Outcome::Timeout,
                None,
                hints_used as u8,
                transcript.as_deref(),
                fault_name,
            )?;
            write_status(HostedStatus {
                session_id: id,
                phase: HostedPhase::TimedOut,
                updated_at: Utc::now(),
                elapsed_secs: None,
                hints_used: Some(hints_used),
                message: None,
            })?;
            println!("\n✗ hosted incident exceeded its SLA");
        }
        Ok(crate::runner::RunResult::Abandoned { transcript }) => {
            recorder::record(
                &scenario.meta.id,
                Outcome::Abandoned,
                None,
                0,
                transcript.as_deref(),
                fault_name,
            )?;
            write_status(HostedStatus {
                session_id: id,
                phase: HostedPhase::Abandoned,
                updated_at: Utc::now(),
                elapsed_secs: None,
                hints_used: Some(0),
                message: None,
            })?;
            println!("\nHosted shell exited before resolution");
        }
        Err(error) => {
            write_status(HostedStatus {
                session_id: id,
                phase: HostedPhase::Failed,
                updated_at: Utc::now(),
                elapsed_secs: None,
                hints_used: None,
                message: Some(error.to_string()),
            })?;
            remove_participant_key(id).ok();
            return Err(error);
        }
    }
    remove_participant_key(id)?;
    Ok(())
}

pub fn status(session_id: &str) -> Result<HostedStatus> {
    let id = parse_id(session_id)?;
    let path = status_path(id);
    let bytes = fs::read(&path).with_context(|| format!("reading {}", path.display()))?;
    serde_json::from_slice(&bytes).context("parsing hosted session status")
}

pub fn ready(session_id: &str) -> Result<()> {
    let id = parse_id(session_id)?;
    write_status(HostedStatus {
        session_id: id,
        phase: HostedPhase::Ready,
        updated_at: Utc::now(),
        elapsed_secs: None,
        hints_used: None,
        message: None,
    })
}

pub fn cleanup(session_id: &str, scenario_path: &Path) -> Result<()> {
    let id = parse_id(session_id)?;
    require_session_scenario(id, scenario_path)?;
    let dir = state_dir(id);
    if let Some(pid) = active_hosted_pid(id, &dir) {
        Command::new("kill").arg(pid.to_string()).status().ok();
    }

    let mut compose_stopped = true;
    if scenario_path.join("docker-compose.yml").exists() {
        compose_stopped = Command::new("docker")
            .args(["compose", "-f"])
            .arg(scenario_path.join("docker-compose.yml"))
            .args(["down", "-v", "--remove-orphans"])
            .env(
                "COMPOSE_PROJECT_NAME",
                format!("replaybook_{}", id.simple()),
            )
            .status()
            .is_ok_and(|status| status.success());
    }
    remove_participant_key(id)?;
    if !compose_stopped {
        bail!("failed to stop the hosted scenario's Compose project");
    }
    fs::remove_dir_all(remote_root(id)).ok();
    write_status(HostedStatus {
        session_id: id,
        phase: HostedPhase::Destroyed,
        updated_at: Utc::now(),
        elapsed_secs: None,
        hints_used: None,
        message: None,
    })?;
    fs::remove_file(dir.join("pid")).ok();
    Ok(())
}

fn entropy() -> usize {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.subsec_nanos() as usize)
        .unwrap_or(0)
}

fn parse_id(value: &str) -> Result<Uuid> {
    Uuid::parse_str(value).context("session ID must be a UUID")
}

fn remote_root(id: Uuid) -> PathBuf {
    PathBuf::from(format!("/tmp/replaybook-hosted/{id}"))
}

fn require_session_scenario(id: Uuid, scenario_path: &Path) -> Result<()> {
    let expected = remote_root(id).join("scenario");
    if scenario_path != expected {
        bail!(
            "hosted scenario path must be {} for session {id}",
            expected.display()
        );
    }
    Ok(())
}

fn hosted_root() -> PathBuf {
    dirs_next::data_local_dir()
        .unwrap_or_else(std::env::temp_dir)
        .join("replaybook/hosted")
}

fn state_dir(id: Uuid) -> PathBuf {
    hosted_root().join(id.to_string())
}

fn status_path(id: Uuid) -> PathBuf {
    state_dir(id).join("status.json")
}

fn write_status(status: HostedStatus) -> Result<()> {
    let dir = state_dir(status.session_id);
    fs::create_dir_all(&dir)?;
    let path = dir.join("status.json");
    let tmp = dir.join("status.json.tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(&status)?)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn remove_participant_key(id: Uuid) -> Result<()> {
    let Some(home) = std::env::var_os("HOME") else {
        return Ok(());
    };
    let path = PathBuf::from(home).join(".ssh/authorized_keys");
    if !path.exists() {
        return Ok(());
    }
    let marker = format!("replaybook-session:{id}");
    let current = fs::read_to_string(&path)?;
    let retained = current
        .lines()
        .filter(|line| !line.contains(&marker))
        .collect::<Vec<_>>();
    let mut content = retained.join("\n");
    if !content.is_empty() {
        content.push('\n');
    }
    let tmp = path.with_extension("replaybook.tmp");
    fs::write(&tmp, content)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn active_hosted_pid(id: Uuid, dir: &Path) -> Option<u32> {
    let pid = fs::read_to_string(dir.join("pid"))
        .ok()?
        .trim()
        .parse::<u32>()
        .ok()?;
    if pid == std::process::id() {
        return None;
    }
    let command_line = fs::read(format!("/proc/{pid}/cmdline")).ok()?;
    let expected_id = id.to_string();
    let fields: Vec<_> = command_line
        .split(|byte| *byte == 0)
        .filter_map(|field| std::str::from_utf8(field).ok())
        .collect();
    (fields.iter().any(|field| field.contains("replaybook"))
        && fields.iter().any(|field| *field == expected_id))
    .then_some(pid)
}

struct HostedRunGuard {
    pid_path: PathBuf,
}

impl HostedRunGuard {
    fn start(id: Uuid) -> Result<Self> {
        let dir = state_dir(id);
        fs::create_dir_all(&dir)?;
        let pid_path = dir.join("pid");
        fs::write(&pid_path, std::process::id().to_string())?;
        Ok(Self { pid_path })
    }
}

impl Drop for HostedRunGuard {
    fn drop(&mut self) {
        let id = self
            .pid_path
            .parent()
            .and_then(Path::file_name)
            .and_then(|value| value.to_str())
            .and_then(|value| Uuid::parse_str(value).ok());
        if let Some(id) = id {
            remove_participant_key(id).ok();
            let terminal =
                status(id.to_string().as_str()).is_ok_and(|status| status.phase.is_terminal());
            if !terminal {
                write_status(HostedStatus {
                    session_id: id,
                    phase: HostedPhase::Failed,
                    updated_at: Utc::now(),
                    elapsed_secs: None,
                    hints_used: None,
                    message: Some("hosted runner exited before recording an outcome".into()),
                })
                .ok();
            }
        }
        fs::remove_file(&self.pid_path).ok();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hosted_scenario_is_confined_to_session_directory() {
        let id = Uuid::new_v4();
        let good = PathBuf::from(format!("/tmp/replaybook-hosted/{id}/scenario"));
        assert!(require_session_scenario(id, &good).is_ok());
        assert!(require_session_scenario(id, Path::new("/tmp/other")).is_err());
        assert!(
            require_session_scenario(
                id,
                &PathBuf::from(format!("/tmp/replaybook-hosted/{id}/scenario/.."))
            )
            .is_err()
        );
    }

    #[test]
    fn invalid_session_ids_are_rejected() {
        assert!(parse_id("../../etc").is_err());
        assert!(parse_id("not-a-uuid").is_err());
    }
}
