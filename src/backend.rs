use crate::runner::{self, RunResult};
use crate::scenario::{ActiveFault, Scenario};
use anyhow::{Context, Result, bail};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use uuid::Uuid;

#[async_trait]
pub trait ExecutionBackend: Send + Sync {
    async fn run(
        &self,
        scenario: &Scenario,
        fault: &ActiveFault,
        sla_seconds: u64,
    ) -> Result<RunResult>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct LocalDockerBackend;

#[async_trait]
impl ExecutionBackend for LocalDockerBackend {
    async fn run(
        &self,
        scenario: &Scenario,
        fault: &ActiveFault,
        sla_seconds: u64,
    ) -> Result<RunResult> {
        runner::run_scenario(scenario, fault, sla_seconds).await
    }
}

#[derive(Debug, Clone)]
pub struct RemoteVmBackend {
    destination: String,
    port: u16,
    ssh_options: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostedSession {
    pub id: Uuid,
    pub scenario_id: String,
    pub destination: String,
    pub ssh_port: u16,
    pub remote_scenario: String,
    pub private_key_path: PathBuf,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub expires_at: chrono::DateTime<chrono::Utc>,
    pub sla_minutes: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fault: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CreatedSession {
    pub session: HostedSession,
    pub ssh_command: String,
    pub private_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum HostedPhase {
    Ready,
    Active,
    Succeeded,
    TimedOut,
    Abandoned,
    Failed,
    Destroyed,
}

impl HostedPhase {
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            Self::Succeeded | Self::TimedOut | Self::Abandoned | Self::Failed | Self::Destroyed
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostedStatus {
    pub session_id: Uuid,
    pub phase: HostedPhase,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub elapsed_secs: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hints_used: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CreateSessionRequest<'a> {
    pub scenario: &'a Scenario,
    pub sla_minutes: u64,
    pub ttl_minutes: u64,
    pub fault: Option<&'a str>,
}

pub trait SessionProvisioner: Send + Sync {
    fn create(&self, request: CreateSessionRequest<'_>) -> Result<CreatedSession>;
    fn status(&self, session: &HostedSession) -> Result<HostedStatus>;
    fn destroy(&self, session: &HostedSession) -> Result<()>;
}

impl RemoteVmBackend {
    pub fn new(destination: impl Into<String>, port: u16) -> Result<Self> {
        let destination = destination.into();
        validate_destination(&destination)?;
        if port == 0 {
            bail!("SSH port must be greater than zero");
        }
        Ok(Self {
            destination,
            port,
            ssh_options: vec![
                "-o".into(),
                "BatchMode=yes".into(),
                "-o".into(),
                "ConnectTimeout=10".into(),
            ],
        })
    }

    pub fn destination(&self) -> &str {
        &self.destination
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    fn session_root() -> PathBuf {
        dirs_next::data_local_dir()
            .unwrap_or_else(std::env::temp_dir)
            .join("replaybook/control/sessions")
    }

    fn ssh_command(&self) -> Command {
        let mut command = Command::new("ssh");
        command.args(&self.ssh_options);
        command.args(["-p", &self.port.to_string(), &self.destination]);
        command
    }

    fn remote_command(&self, command: &str) -> Result<std::process::Output> {
        self.ssh_command()
            .arg(command)
            .output()
            .with_context(|| format!("running ssh to {}", self.destination))
    }

    fn require_remote_tools(&self) -> Result<()> {
        let output = self.remote_command(
            "command -v replaybook >/dev/null && command -v docker >/dev/null && docker compose version >/dev/null",
        )?;
        if !output.status.success() {
            bail!(
                "remote VM must have replaybook and Docker Compose installed: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        Ok(())
    }

    fn stage_scenario(&self, scenario: &Scenario, remote_dir: &str) -> Result<()> {
        let mut tar = Command::new("tar")
            .args(["-C"])
            .arg(&scenario.dir)
            .args(["-czf", "-", "."])
            .stdout(Stdio::piped())
            .spawn()
            .context("starting tar to stage scenario")?;
        let tar_stdout = tar.stdout.take().context("capturing tar output")?;
        let command = format!(
            "umask 077; mkdir -p {} && tar -xzf - -C {}",
            shell_quote(remote_dir),
            shell_quote(remote_dir)
        );
        let ssh_status = self
            .ssh_command()
            .arg(command)
            .stdin(Stdio::from(tar_stdout))
            .status()
            .context("staging scenario over ssh")?;
        let tar_status = tar.wait().context("waiting for scenario archive")?;
        if !tar_status.success() || !ssh_status.success() {
            bail!("failed to stage scenario on remote VM");
        }
        Ok(())
    }

    fn validate_staged_scenario(&self, remote_dir: &str) -> Result<()> {
        let command = format!("replaybook validate {}", shell_quote(remote_dir));
        let output = self.remote_command(&command)?;
        if !output.status.success() {
            bail!(
                "staged scenario failed remote validation: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        Ok(())
    }

    fn initialize_remote_status(&self, session: &HostedSession) -> Result<()> {
        let command = format!("replaybook hosted-ready --session {}", session.id);
        let output = self.remote_command(&command)?;
        if !output.status.success() {
            bail!(
                "failed to initialize remote session status: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        Ok(())
    }

    fn install_forced_key(&self, session: &HostedSession, public_key: &str) -> Result<()> {
        let mut hosted_run = format!(
            "replaybook hosted-run --session {} --scenario {} --sla {}",
            session.id,
            shell_quote(&session.remote_scenario),
            session.sla_minutes
        );
        if let Some(fault) = &session.fault {
            hosted_run.push_str(" --fault ");
            hosted_run.push_str(&shell_quote(fault));
        }
        let line = participant_authorized_key(&hosted_run, public_key, session.id);
        let mut child = self
            .ssh_command()
            .arg("umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; cat >> ~/.ssh/authorized_keys")
            .stdin(Stdio::piped())
            .spawn()
            .context("installing participant SSH key")?;
        child
            .stdin
            .take()
            .context("opening ssh stdin")?
            .write_all(line.as_bytes())?;
        let status = child.wait()?;
        if !status.success() {
            bail!("failed to install participant SSH key");
        }
        Ok(())
    }

    fn ssh_participant(&self, session: &HostedSession) -> Result<std::process::ExitStatus> {
        Command::new("ssh")
            .args(["-tt", "-i"])
            .arg(&session.private_key_path)
            .args(["-p", &self.port.to_string()])
            .arg(&self.destination)
            .status()
            .context("attaching to hosted session")
    }

    fn connection_command(&self, key_path: &Path) -> String {
        format!(
            "ssh -tt -i {} -p {} {}",
            shell_quote(&key_path.display().to_string()),
            self.port,
            self.destination
        )
    }
}

impl SessionProvisioner for RemoteVmBackend {
    fn create(&self, request: CreateSessionRequest<'_>) -> Result<CreatedSession> {
        if request.sla_minutes == 0 || request.ttl_minutes == 0 {
            bail!("SLA and TTL must be greater than zero");
        }
        self.require_remote_tools()?;

        let id = Uuid::new_v4();
        let local_dir = Self::session_root().join(id.to_string());
        fs::create_dir_all(&local_dir)?;
        let key_path = local_dir.join("id_ed25519");
        let key_status = Command::new("ssh-keygen")
            .args(["-q", "-t", "ed25519", "-N", "", "-C"])
            .arg(format!("replaybook-session:{id}"))
            .arg("-f")
            .arg(&key_path)
            .status()
            .context("generating participant SSH key")?;
        if !key_status.success() {
            fs::remove_dir_all(&local_dir).ok();
            bail!("ssh-keygen failed");
        }

        let remote_scenario = format!("/tmp/replaybook-hosted/{id}/scenario");
        let now = chrono::Utc::now();
        let session = HostedSession {
            id,
            scenario_id: request.scenario.meta.id.clone(),
            destination: self.destination.clone(),
            ssh_port: self.port,
            remote_scenario,
            private_key_path: key_path.clone(),
            created_at: now,
            expires_at: now + chrono::Duration::minutes(request.ttl_minutes as i64),
            sla_minutes: request.sla_minutes,
            fault: request.fault.map(String::from),
        };

        let provision = (|| {
            self.stage_scenario(request.scenario, &session.remote_scenario)?;
            self.validate_staged_scenario(&session.remote_scenario)?;
            self.initialize_remote_status(&session)?;
            let public_key = fs::read_to_string(key_path.with_extension("pub"))?;
            self.install_forced_key(&session, &public_key)?;
            let private_key = fs::read_to_string(&key_path)?;
            Ok(CreatedSession {
                ssh_command: self.connection_command(&key_path),
                session: session.clone(),
                private_key,
            })
        })();
        if provision.is_err() {
            self.destroy(&session).ok();
            fs::remove_dir_all(&local_dir).ok();
        }
        provision
    }

    fn status(&self, session: &HostedSession) -> Result<HostedStatus> {
        let command = format!("replaybook hosted-status --session {}", session.id);
        let output = self.remote_command(&command)?;
        if !output.status.success() {
            bail!(
                "failed to read remote session status: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        serde_json::from_slice(&output.stdout).context("parsing remote session status")
    }

    fn destroy(&self, session: &HostedSession) -> Result<()> {
        let command = format!(
            "replaybook hosted-cleanup --session {} --scenario {}",
            session.id,
            shell_quote(&session.remote_scenario)
        );
        let output = self.remote_command(&command)?;
        fs::remove_dir_all(Self::session_root().join(session.id.to_string())).ok();
        if !output.status.success() {
            bail!(
                "remote session cleanup failed: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        Ok(())
    }
}

#[async_trait]
impl ExecutionBackend for RemoteVmBackend {
    async fn run(
        &self,
        scenario: &Scenario,
        fault: &ActiveFault,
        sla_seconds: u64,
    ) -> Result<RunResult> {
        let fault_name = fault.name.as_deref();
        let created = self.create(CreateSessionRequest {
            scenario,
            sla_minutes: sla_seconds.div_ceil(60),
            ttl_minutes: sla_seconds.div_ceil(60) + 15,
            fault: fault_name,
        })?;
        println!("[replaybook] remote session: {}", created.session.id);
        println!("[replaybook] connect: {}", created.ssh_command);
        let attach = self.ssh_participant(&created.session);
        let status = self.status(&created.session);
        let cleanup = self.destroy(&created.session);
        cleanup?;
        let attach = attach?;
        let status = status?;
        if !attach.success() && matches!(status.phase, HostedPhase::Ready | HostedPhase::Active) {
            bail!("remote SSH session exited unsuccessfully");
        }
        match status.phase {
            HostedPhase::Succeeded => Ok(RunResult::Success {
                elapsed: std::time::Duration::from_secs(status.elapsed_secs.unwrap_or(0)),
                hints_used: status.hints_used.unwrap_or(0),
                transcript: None,
            }),
            HostedPhase::TimedOut => Ok(RunResult::Timeout {
                hints_used: status.hints_used.unwrap_or(0),
                transcript: None,
            }),
            _ => Ok(RunResult::Abandoned { transcript: None }),
        }
    }
}

fn validate_destination(destination: &str) -> Result<()> {
    let mut parts = destination.split('@');
    let first = parts.next().unwrap_or_default();
    let second = parts.next();
    if parts.next().is_some() {
        bail!("SSH destination must be [user@]host");
    }
    let (user, host) = match second {
        Some(host) => (Some(first), host),
        None => (None, first),
    };
    let valid_user = user.is_none_or(|value| {
        !value.is_empty()
            && value
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
    });
    let valid_host = !host.is_empty()
        && host
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.'))
        && !host.starts_with('-');
    if !valid_user || !valid_host {
        bail!("SSH destination must be [user@]host using a DNS name or IPv4 address");
    }
    Ok(())
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn authorized_key_command(command: &str) -> String {
    command.replace('\\', "\\\\").replace('"', "\\\"")
}

fn participant_authorized_key(command: &str, public_key: &str, id: Uuid) -> String {
    let forced = authorized_key_command(command);
    format!(
        "restrict,pty,command=\"{forced}\" {} replaybook-session:{id}\n",
        public_key.trim()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn destination_validation_rejects_ssh_option_injection() {
        for bad in [
            "",
            "-oProxyCommand=oops",
            "user@host@other",
            "user name@host",
            "user@host;rm",
            "user@[::1]",
        ] {
            assert!(validate_destination(bad).is_err(), "accepted {bad:?}");
        }
        for good in ["training.example.com", "replay@10.0.0.8", "user_name@vm-1"] {
            assert!(validate_destination(good).is_ok(), "rejected {good:?}");
        }
    }

    #[test]
    fn shell_quote_handles_apostrophes() {
        assert_eq!(shell_quote("it's"), "'it'\"'\"'s'");
    }

    #[test]
    fn authorized_key_command_escapes_option_delimiters() {
        assert_eq!(
            authorized_key_command("echo \"hi\" \\ done"),
            "echo \\\"hi\\\" \\\\ done"
        );
    }

    #[test]
    fn participant_key_allows_only_the_forced_interactive_session() {
        let id = Uuid::nil();
        let line = participant_authorized_key("replaybook hosted-run", "ssh-ed25519 AAAA", id);
        assert!(
            line.starts_with("restrict,pty,command=\"replaybook hosted-run\" ssh-ed25519 AAAA ")
        );
        assert!(line.ends_with(&format!("replaybook-session:{id}\n")));
    }

    #[test]
    fn terminal_phase_classification_is_complete() {
        assert!(!HostedPhase::Ready.is_terminal());
        assert!(!HostedPhase::Active.is_terminal());
        assert!(HostedPhase::Succeeded.is_terminal());
        assert!(HostedPhase::Destroyed.is_terminal());
    }
}
