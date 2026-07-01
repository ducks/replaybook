use crate::scenario::{Scenario, SuccessCondition};
use anyhow::{Result, bail};
use std::fs;
use std::process::Command;
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
use std::time::{Duration, Instant};

const HINT_FLAG: &str = "/tmp/on-call-hint";
const TMUX_SESSION: &str = "on-call";

pub struct StateFile {
    pub host_path: std::path::PathBuf,
}

impl StateFile {
    fn new(scenario_id: &str) -> Result<Self> {
        let path = std::env::temp_dir().join(format!("on-call-state-{}", scenario_id));
        fs::write(&path, "")?;
        Ok(Self { host_path: path })
    }

    pub fn container_path() -> &'static str {
        "/tmp/on-call-state"
    }

    fn write(
        &self,
        status: &str,
        remaining: u64,
        hints_used: usize,
        hint_total: usize,
        revealed: &[String],
    ) {
        let mut content = format!("{status}\n{remaining}\n{hints_used}\n{hint_total}\n");
        for h in revealed {
            content.push_str(h);
            content.push('\n');
        }
        fs::write(&self.host_path, content).ok();
    }
}

impl Drop for StateFile {
    fn drop(&mut self) {
        fs::remove_file(&self.host_path).ok();
    }
}

pub async fn run_scenario(scenario: &Scenario, sla_seconds: u64) -> Result<RunResult> {
    // Touch state file before compose up so bind mount sees a file not a dir
    let state = StateFile::new(&scenario.meta.id)?;

    println!("\n[on-call] starting environment...");
    compose_down(scenario);
    compose_up(scenario, &state)?;
    std::thread::sleep(Duration::from_secs(2));

    println!("[on-call] injecting fault...");
    run_script(scenario.break_script())?;

    let container = primary_container(scenario)?;

    println!("[on-call] setting up environment...");
    install_tmux(&container)?;
    inject_tools(&container, scenario)?;

    state.write("ACTIVE", sla_seconds, 0, scenario.meta.hints.len(), &[]);

    let solved = Arc::new(AtomicBool::new(false));
    let timed_out = Arc::new(AtomicBool::new(false));
    let cancelled = Arc::new(AtomicBool::new(false));
    let hints_used = Arc::new(AtomicUsize::new(0));

    let solved_bg = solved.clone();
    let timed_out_bg = timed_out.clone();
    let cancelled_bg = cancelled.clone();
    let hints_used_bg = hints_used.clone();
    let scenario_dir = scenario.dir.clone();
    let check_script = scenario.check_script();
    let success_condition = scenario.meta.success_condition.clone();
    let success_target = scenario.meta.success_target.clone();
    let deadline = Duration::from_secs(sla_seconds);
    let hints = scenario.meta.hints.clone();
    let container_bg = container.clone();
    let hint_count = hints.len();
    let state_path = state.host_path.clone();

    let poller = tokio::task::spawn_blocking(move || {
        let started = Instant::now();
        loop {
            std::thread::sleep(Duration::from_secs(2));

            if cancelled_bg.load(Ordering::SeqCst) {
                return;
            }

            let elapsed = started.elapsed();

            if elapsed >= deadline {
                timed_out_bg.store(true, Ordering::SeqCst);
                let used = hints_used_bg.load(Ordering::SeqCst);
                let revealed: Vec<String> = hints[..used].to_vec();
                fs::write(
                    &state_path,
                    format!("TIMEOUT\n0\n{used}\n{hint_count}\n{}", revealed.join("\n")),
                )
                .ok();
                return;
            }

            let remaining = deadline.saturating_sub(elapsed).as_secs();

            // Check for hint request (flag file inside container, visible on host via mount)
            let hint_flag_path =
                std::env::temp_dir().join(format!("on-call-hint-{}", container_bg.trim()));
            // Hint flag is written inside container at /tmp/on-call-hint
            // We detect it via docker exec (cheap test -f)
            let hint_flag_exists = Command::new("docker")
                .args(["exec", &container_bg, "test", "-f", HINT_FLAG])
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            drop(hint_flag_path);

            if hint_flag_exists {
                Command::new("docker")
                    .args(["exec", &container_bg, "rm", "-f", HINT_FLAG])
                    .status()
                    .ok();

                let idx = hints_used_bg.fetch_add(1, Ordering::SeqCst);
                if idx >= hints.len() {
                    hints_used_bg.fetch_sub(1, Ordering::SeqCst);
                }
            }

            let used = hints_used_bg.load(Ordering::SeqCst);
            let revealed: Vec<String> = hints[..used].to_vec();

            let ok = match success_condition {
                SuccessCondition::Http200 => Command::new("curl")
                    .args(["-sf", "--max-time", "4", &success_target])
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false),
                SuccessCondition::ExitZero => {
                    if check_script.exists() {
                        Command::new("bash")
                            .arg(&check_script)
                            .current_dir(&scenario_dir)
                            .status()
                            .map(|s| s.success())
                            .unwrap_or(false)
                    } else {
                        false
                    }
                }
            };

            let status = if ok { "SOLVED" } else { "ACTIVE" };
            let mut content = format!("{status}\n{remaining}\n{used}\n{hint_count}\n");
            for h in &revealed {
                content.push_str(h);
                content.push('\n');
            }
            fs::write(&state_path, content).ok();

            if ok {
                solved_bg.store(true, Ordering::SeqCst);
                return;
            }
        }
    });

    let started = Instant::now();

    // Create session detached, split HUD pane, then attach
    Command::new("docker")
        .args([
            "exec",
            &container,
            "tmux",
            "new-session",
            "-d",
            "-s",
            TMUX_SESSION,
        ])
        .status()
        .ok();
    Command::new("docker")
        .args([
            "exec",
            &container,
            "tmux",
            "split-window",
            "-h",
            "-l",
            "44",
            "-t",
            TMUX_SESSION,
            "/usr/local/bin/on-call-hud",
        ])
        .status()
        .ok();
    Command::new("docker")
        .args([
            "exec",
            &container,
            "tmux",
            "select-pane",
            "-t",
            &format!("{}:0.0", TMUX_SESSION),
        ])
        .status()
        .ok();
    Command::new("docker")
        .args([
            "exec",
            "-it",
            &container,
            "tmux",
            "attach-session",
            "-t",
            TMUX_SESSION,
        ])
        .status()
        .ok();

    cancelled.store(true, Ordering::SeqCst);
    poller.abort();
    std::thread::sleep(Duration::from_millis(500));

    let elapsed = started.elapsed();
    let used = hints_used.load(Ordering::SeqCst);

    compose_down(scenario);
    // state file cleaned up by StateFile::drop

    if solved.load(Ordering::SeqCst) {
        return Ok(RunResult::Success {
            elapsed,
            hints_used: used,
        });
    }
    if timed_out.load(Ordering::SeqCst) {
        return Ok(RunResult::Timeout { hints_used: used });
    }
    Ok(RunResult::Abandoned)
}

fn install_tmux(container: &str) -> Result<()> {
    Command::new("docker")
        .args(["exec", container, "apk", "add", "--no-cache", "-q", "tmux"])
        .status()
        .ok();
    Ok(())
}

fn inject_tools(container: &str, scenario: &Scenario) -> Result<()> {
    let container_state = StateFile::container_path();
    let page = &scenario.meta.page;

    let get_hint = b"#!/bin/sh\ntouch /tmp/on-call-hint\necho 'Hint requested...'\n";
    cp_bytes(container, get_hint, "/usr/local/bin/get-hint")?;

    let hud = format!(
        r#"#!/bin/sh
PAGE='{page}'
STATE={container_state}
while true; do
  printf '\033[2J\033[H'
  printf '\033[1;36m== on-call ==\033[0m\n\n'
  printf '\033[1mINCIDENT:\033[0m\n'
  printf '%s\n' "$PAGE" | fold -s -w 40
  printf '\n'
  if [ -f "$STATE" ]; then
    STATUS=$(sed -n '1p' "$STATE")
    REMAINING=$(sed -n '2p' "$STATE")
    HINTS_USED=$(sed -n '3p' "$STATE")
    HINT_TOTAL=$(sed -n '4p' "$STATE")
    MINS=$((REMAINING / 60)); SECS=$((REMAINING % 60))
    case "$STATUS" in
      SOLVED)  printf '\033[1;32mSTATUS: RESOLVED\033[0m\n' ;;
      TIMEOUT) printf '\033[1;31mSTATUS: SLA BREACHED\033[0m\n' ;;
      *)       printf 'STATUS: \033[1;33mACTIVE\033[0m\n' ;;
    esac
    printf 'SLA:    %02d:%02d remaining\n' "$MINS" "$SECS"
    printf 'HINTS:  %s / %s  (run get-hint for next)\n\n' "$HINTS_USED" "$HINT_TOTAL"
    N=1; tail -n +5 "$STATE" | while IFS= read -r h; do
      printf '\033[1;33m[hint %d]\033[0m %s\n' "$N" "$h"; N=$((N+1))
    done
  fi
  sleep 2
done
"#
    );
    cp_bytes(container, hud.as_bytes(), "/usr/local/bin/on-call-hud")?;

    let tmux_conf = b"set -g status off\n";
    cp_bytes(container, tmux_conf, "/root/.tmux.conf")?;

    Ok(())
}

fn cp_bytes(container: &str, contents: &[u8], dest: &str) -> Result<()> {
    // Write to a temp file on the host then docker cp it in
    let tmp = std::env::temp_dir().join(format!("on-call-inject-{}", dest.replace('/', "_")));
    fs::write(&tmp, contents)?;
    Command::new("docker")
        .args([
            "cp",
            tmp.to_str().unwrap(),
            &format!("{}:{}", container, dest),
        ])
        .status()
        .ok();
    Command::new("docker")
        .args(["exec", container, "chmod", "+x", dest])
        .status()
        .ok();
    fs::remove_file(&tmp).ok();
    Ok(())
}

fn first_compose_service(scenario: &Scenario) -> Result<String> {
    let output = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["config", "--services"])
        .output()?;

    std::str::from_utf8(&output.stdout)?
        .lines()
        .find(|l| !l.is_empty())
        .map(|s| s.trim().to_string())
        .ok_or_else(|| {
            anyhow::anyhow!("no services found in {}", scenario.compose_file().display())
        })
}

fn compose_up(scenario: &Scenario, state: &StateFile) -> Result<()> {
    // Write a compose override that bind-mounts the state file into the container
    let override_path = scenario.dir.join("docker-compose.override.yml");
    let host_path = state.host_path.display();
    let container_path = StateFile::container_path();

    let service = match scenario.meta.shell_service.as_deref() {
        Some(service) => service.to_string(),
        None => first_compose_service(scenario)?,
    };
    let override_content =
        format!("services:\n  {service}:\n    volumes:\n      - {host_path}:{container_path}\n");
    fs::write(&override_path, override_content)?;

    let status = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["-f", override_path.to_str().unwrap()])
        .args(["up", "-d", "--build"])
        .status()?;

    fs::remove_file(&override_path).ok();

    if !status.success() {
        bail!("docker compose up failed");
    }
    Ok(())
}

fn compose_down(scenario: &Scenario) {
    Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["down", "-v", "--remove-orphans"])
        .status()
        .ok();
}

fn primary_container(scenario: &Scenario) -> Result<String> {
    let output = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["ps", "-q"])
        .output()?;

    let ids: Vec<&str> = std::str::from_utf8(&output.stdout)?
        .lines()
        .filter(|l| !l.is_empty())
        .collect();

    let target = scenario.meta.shell_service.as_deref().unwrap_or("");
    if !target.is_empty() {
        let name_output = Command::new("docker")
            .args(["compose", "-f"])
            .arg(scenario.compose_file())
            .args(["ps", "-q", target])
            .output()?;
        let id = std::str::from_utf8(&name_output.stdout)?.trim().to_string();
        if !id.is_empty() {
            return Ok(id);
        }
    }

    ids.first()
        .map(|s| s.trim().to_string())
        .ok_or_else(|| anyhow::anyhow!("no containers found"))
}

fn run_script(path: std::path::PathBuf) -> Result<()> {
    if !path.exists() {
        return Ok(());
    }
    let status = Command::new("bash").arg(&path).status()?;
    if !status.success() {
        bail!("script {} failed", path.display());
    }
    Ok(())
}

pub enum RunResult {
    Success {
        elapsed: Duration,
        hints_used: usize,
    },
    Timeout {
        hints_used: usize,
    },
    Abandoned,
}
