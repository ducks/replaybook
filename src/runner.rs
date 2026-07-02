use crate::scenario::{BreakStep, Scenario, SuccessCondition};
use anyhow::{Result, bail};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
use std::time::{Duration, Instant};

const HINT_FLAG: &str = "/tmp/on-call-hint";
const TMUX_SESSION: &str = "on-call";
/// The service the player is dropped into: a jumphost on the incident's
/// network with the docker CLI (docker exec is your ssh).
const WORKSTATION_SERVICE: &str = "replaybook-workstation";
const WORKSTATION_IMAGE: &str = "docker:cli";

fn docker_exec(container: &str, args: &[&str]) -> bool {
    let mut cmd_args = vec!["exec", container];
    cmd_args.extend_from_slice(args);
    Command::new("docker")
        .args(cmd_args)
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

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

struct ComposeGuard<'a> {
    scenario: &'a Scenario,
}

impl Drop for ComposeGuard<'_> {
    fn drop(&mut self) {
        compose_down(self.scenario);
        fs::remove_file(override_path(self.scenario)).ok();
    }
}

fn check_success(
    condition: &SuccessCondition,
    target: &str,
    check_script: &Path,
    scenario_dir: &Path,
) -> bool {
    match condition {
        SuccessCondition::Http200 => Command::new("curl")
            .args(["-sf", "--max-time", "4", target])
            .status()
            .map(|s| s.success())
            .unwrap_or(false),
        SuccessCondition::ExitZero => {
            if check_script.exists() {
                Command::new("bash")
                    .arg(check_script)
                    .current_dir(scenario_dir)
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false)
            } else {
                false
            }
        }
    }
}

pub async fn run_scenario(scenario: &Scenario, sla_seconds: u64) -> Result<RunResult> {
    // Touch state file before compose up so bind mount sees a file not a dir
    let state = StateFile::new(&scenario.meta.id)?;

    println!("\n[replaybook] starting environment...");
    compose_down(scenario);
    // Tear the compose stack down on any exit path, including early returns
    // from setup steps that fail below.
    let _compose_guard = ComposeGuard { scenario };
    let workstation = compose_up(scenario, &state)?;
    std::thread::sleep(Duration::from_secs(2));

    println!("[replaybook] injecting fault...");
    inject_fault(scenario)?;

    println!("[replaybook] preparing workstation...");
    setup_workstation(&workstation)?;
    inject_tools(&workstation, scenario)?;

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
    let container_bg = workstation.clone();
    let hint_count = hints.len();
    let state_path = state.host_path.clone();

    let _poller = tokio::task::spawn_blocking(move || {
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

            // Hint flag is written inside the workstation at /tmp/on-call-hint.
            // We detect it via docker exec (cheap test -f).
            let hint_flag_exists = docker_exec(&container_bg, &["test", "-f", HINT_FLAG]);

            if hint_flag_exists {
                docker_exec(&container_bg, &["rm", "-f", HINT_FLAG]);

                hints_used_bg
                    .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |used| {
                        if used < hints.len() {
                            Some(used + 1)
                        } else {
                            None
                        }
                    })
                    .ok();
            }

            let used = hints_used_bg.load(Ordering::SeqCst);
            let revealed: Vec<String> = hints[..used].to_vec();

            let ok = check_success(
                &success_condition,
                &success_target,
                &check_script,
                &scenario_dir,
            );

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
    if !docker_exec(
        &workstation,
        &["tmux", "new-session", "-d", "-s", TMUX_SESSION],
    ) {
        bail!("failed to start tmux in the workstation container");
    }
    if !docker_exec(
        &workstation,
        &[
            "tmux",
            "split-window",
            "-h",
            "-l",
            "44",
            "-t",
            TMUX_SESSION,
            "/usr/local/bin/on-call-hud",
        ],
    ) {
        bail!("failed to open the HUD pane in the workstation container");
    }
    docker_exec(
        &workstation,
        &[
            "tmux",
            "select-pane",
            "-t",
            &format!("{}:0.0", TMUX_SESSION),
        ],
    );
    Command::new("docker")
        .args([
            "exec",
            "-it",
            &workstation,
            "tmux",
            "attach-session",
            "-t",
            TMUX_SESSION,
        ])
        .status()
        .ok();

    cancelled.store(true, Ordering::SeqCst);

    let elapsed = started.elapsed();
    let used = hints_used.load(Ordering::SeqCst);

    // The player may fix the issue and exit the shell inside the poller's 2s
    // window - do one final check before classifying the run as abandoned.
    if !solved.load(Ordering::SeqCst)
        && !timed_out.load(Ordering::SeqCst)
        && check_success(
            &scenario.meta.success_condition,
            &scenario.meta.success_target,
            &scenario.check_script(),
            &scenario.dir,
        )
    {
        solved.store(true, Ordering::SeqCst);
    }

    // compose stack torn down by ComposeGuard::drop, state file by StateFile::drop

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

/// Test a scenario end-to-end without a player:
/// up -> break -> assert the check fails -> solve.sh -> assert the check passes.
pub fn test_scenario(scenario: &Scenario) -> Result<()> {
    let solve = scenario.solve_script();
    if !solve.exists() {
        bail!(
            "{} has no solve.sh - replaybook test needs one to verify the fix path",
            scenario.meta.id
        );
    }

    let state = StateFile::new(&scenario.meta.id)?;
    println!("[replaybook] test: starting environment...");
    compose_down(scenario);
    let _compose_guard = ComposeGuard { scenario };
    compose_up(scenario, &state)?;
    std::thread::sleep(Duration::from_secs(2));

    println!("[replaybook] test: injecting fault...");
    inject_fault(scenario)?;

    // Give the fault a moment to take effect (processes die, checks settle).
    std::thread::sleep(Duration::from_secs(5));
    if scenario_check(scenario) {
        bail!(
            "check passes while the fault is applied - the fault didn't take or the check is wrong"
        );
    }
    println!("[replaybook] test: check fails while broken (good)");

    println!("[replaybook] test: applying solve.sh...");
    run_script(&solve, &scenario.dir)?;

    let deadline = Instant::now() + Duration::from_secs(90);
    loop {
        if scenario_check(scenario) {
            break;
        }
        if Instant::now() >= deadline {
            bail!(
                "check still failing 90s after solve.sh - the solve is wrong or the check cannot detect recovery"
            );
        }
        std::thread::sleep(Duration::from_secs(2));
    }
    println!("[replaybook] test: check passes after solve (good)");
    Ok(())
}

fn scenario_check(scenario: &Scenario) -> bool {
    check_success(
        &scenario.meta.success_condition,
        &scenario.meta.success_target,
        &scenario.check_script(),
        &scenario.dir,
    )
}

fn inject_fault(scenario: &Scenario) -> Result<()> {
    match &scenario.meta.break_steps {
        Some(steps) => run_break_steps(scenario, steps),
        None => run_script(&scenario.break_script(), &scenario.dir),
    }
}

fn setup_workstation(container: &str) -> Result<()> {
    if docker_exec(container, &["sh", "-c", "command -v tmux >/dev/null 2>&1"]) {
        return Ok(());
    }
    if docker_exec(
        container,
        &["apk", "add", "--no-cache", "-q", "tmux", "curl"],
    ) {
        return Ok(());
    }
    if docker_exec(
        container,
        &[
            "sh",
            "-c",
            "apt-get update -qq && apt-get install -yqq tmux curl",
        ],
    ) {
        return Ok(());
    }
    bail!("could not install tmux in the workstation container (tried apk and apt-get)");
}

fn inject_tools(container: &str, scenario: &Scenario) -> Result<()> {
    let container_state = StateFile::container_path();
    let page = &scenario.meta.page;

    let get_hint = b"#!/bin/sh\ntouch /tmp/on-call-hint\necho 'Hint requested...'\n";
    cp_bytes(container, get_hint, "/usr/local/bin/get-hint")?;

    // The page text is written to its own file and read back with cat so
    // quotes/apostrophes in the page can't break the HUD script.
    cp_bytes(container, page.as_bytes(), "/etc/on-call-page")?;

    let hud = format!(
        r#"#!/bin/sh
STATE={container_state}
while true; do
  printf '\033[2J\033[H'
  printf '\033[1;36m== on-call ==\033[0m\n\n'
  printf '\033[1mINCIDENT:\033[0m\n'
  cat /etc/on-call-page | fold -s -w 40
  printf '\n\n'
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
    let status = Command::new("docker")
        .args([
            "cp",
            tmp.to_str().unwrap(),
            &format!("{}:{}", container, dest),
        ])
        .status()?;
    fs::remove_file(&tmp).ok();
    if !status.success() {
        bail!("failed to copy {dest} into the workstation container");
    }
    docker_exec(container, &["chmod", "+x", dest]);
    Ok(())
}

fn override_path(scenario: &Scenario) -> PathBuf {
    scenario.dir.join("docker-compose.replaybook.yml")
}

/// Networks defined by the scenario's compose file, so the workstation can be
/// attached to all of them. Falls back to the implicit default network.
fn compose_networks(scenario: &Scenario) -> Vec<String> {
    let fallback = || vec!["default".to_string()];
    let output = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["config", "--format", "json"])
        .output();
    let Ok(output) = output else {
        return fallback();
    };
    let Ok(config) = serde_json::from_slice::<serde_json::Value>(&output.stdout) else {
        return fallback();
    };
    match config.get("networks").and_then(|n| n.as_object()) {
        Some(map) if !map.is_empty() => map.keys().cloned().collect(),
        _ => fallback(),
    }
}

/// Brings the stack up plus a workstation container joined to the scenario's
/// networks, with the docker CLI and the HUD state file mounted. Returns the
/// workstation's container ID.
fn compose_up(scenario: &Scenario, state: &StateFile) -> Result<String> {
    let override_file = override_path(scenario);
    let host_path = state.host_path.display();
    let container_path = StateFile::container_path();
    let networks = compose_networks(scenario);

    let mut content = format!(
        "services:\n  {WORKSTATION_SERVICE}:\n    image: {WORKSTATION_IMAGE}\n    command: [\"sleep\", \"infinity\"]\n    working_dir: /root\n    volumes:\n      - {host_path}:{container_path}\n      - /var/run/docker.sock:/var/run/docker.sock\n    networks:\n"
    );
    for n in &networks {
        content.push_str(&format!("      - {n}\n"));
    }
    content.push_str("networks:\n");
    for n in &networks {
        content.push_str(&format!("  {n}: {{}}\n"));
    }
    fs::write(&override_file, content)?;

    let status = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .arg("-f")
        .arg(&override_file)
        .args(["up", "-d", "--build"])
        .status()?;

    if !status.success() {
        bail!("docker compose up failed");
    }

    let output = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .arg("-f")
        .arg(&override_file)
        .args(["ps", "-q", WORKSTATION_SERVICE])
        .output()?;

    let id = std::str::from_utf8(&output.stdout)?.trim().to_string();
    if id.is_empty() {
        bail!("workstation container did not start");
    }
    Ok(id)
}

fn compose_down(scenario: &Scenario) {
    // --remove-orphans also removes the workstation container, which is only
    // defined in the (already deleted) override file.
    Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["down", "-v", "--remove-orphans"])
        .status()
        .ok();
}

fn run_script(path: &Path, cwd: &Path) -> Result<()> {
    if !path.exists() {
        return Ok(());
    }
    let status = Command::new("bash").arg(path).current_dir(cwd).status()?;
    if !status.success() {
        bail!("script {} failed", path.display());
    }
    Ok(())
}

fn run_break_steps(scenario: &Scenario, steps: &[BreakStep]) -> Result<()> {
    for step in steps {
        match step {
            BreakStep::Cp { service, src, dest } => {
                let host_path = scenario.dir.join(src);
                let status = Command::new("docker")
                    .args(["compose", "-f"])
                    .arg(scenario.compose_file())
                    .args(["cp", host_path.to_str().unwrap()])
                    .arg(format!("{service}:{dest}"))
                    .status()?;
                if !status.success() {
                    bail!("break step failed: cp {src} to {service}:{dest}");
                }
            }
            BreakStep::Exec { service, cmd } => {
                let status = Command::new("docker")
                    .args(["compose", "-f"])
                    .arg(scenario.compose_file())
                    .args(["exec", "-T", service])
                    .args(cmd)
                    .status()?;
                if !status.success() {
                    bail!("break step failed: exec in {service}: {}", cmd.join(" "));
                }
            }
            BreakStep::Restart { service } => {
                let status = Command::new("docker")
                    .args(["compose", "-f"])
                    .arg(scenario.compose_file())
                    .args(["restart", service])
                    .status()?;
                if !status.success() {
                    bail!("break step failed: restart {service}");
                }
            }
        }
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
