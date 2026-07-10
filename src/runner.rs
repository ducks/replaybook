use crate::scenario::{ActiveFault, BreakStep, Scenario, SuccessCondition};
use anyhow::{Result, bail};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
use std::time::{Duration, Instant};

const HINT_FLAG: &str = "/tmp/on-call-hint";
const TMUX_SESSION: &str = "on-call";
const TRANSCRIPT_PATH: &str = "/tmp/on-call-transcript";
const FAULT_SETTLE: Duration = Duration::from_secs(5);
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

#[derive(Default)]
struct Resolution {
    solved: AtomicBool,
    elapsed: Mutex<Option<Duration>>,
}

impl Resolution {
    fn mark_solved(&self, elapsed: Duration) {
        let mut recorded = self
            .elapsed
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if recorded.is_none() {
            *recorded = Some(elapsed);
        }
        self.solved.store(true, Ordering::SeqCst);
    }

    fn is_solved(&self) -> bool {
        self.solved.load(Ordering::SeqCst)
    }

    fn elapsed_or(&self, fallback: Duration) -> Duration {
        self.elapsed
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .unwrap_or(fallback)
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

pub async fn run_scenario(
    scenario: &Scenario,
    fault: &ActiveFault,
    sla_seconds: u64,
) -> Result<RunResult> {
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
    inject_fault(scenario, fault)?;
    std::thread::sleep(FAULT_SETTLE);
    ensure_fault_active(scenario)?;

    println!("[replaybook] preparing workstation...");
    setup_workstation(&workstation)?;
    inject_tools(&workstation, scenario)?;
    setup_tmux(&workstation)?;

    state.write("ACTIVE", sla_seconds, 0, fault.hints.len(), &[]);

    // One clock drives both the SLA and the recorded score. Start it only
    // after the environment and terminal are ready for the player.
    let run_started = Instant::now();
    let resolution = Arc::new(Resolution::default());
    let timed_out = Arc::new(AtomicBool::new(false));
    let cancelled = Arc::new(AtomicBool::new(false));
    let hints_used = Arc::new(AtomicUsize::new(0));

    let resolution_bg = resolution.clone();
    let timed_out_bg = timed_out.clone();
    let cancelled_bg = cancelled.clone();
    let hints_used_bg = hints_used.clone();
    let scenario_dir = scenario.dir.clone();
    let check_script = scenario.check_script();
    let success_condition = scenario.meta.success_condition.clone();
    let success_target = scenario.meta.success_target.clone();
    let deadline = Duration::from_secs(sla_seconds);
    let hints = fault.hints.clone();
    let container_bg = workstation.clone();
    let hint_count = hints.len();
    let state_path = state.host_path.clone();

    let _poller = tokio::task::spawn_blocking(move || {
        loop {
            std::thread::sleep(Duration::from_secs(2));

            if cancelled_bg.load(Ordering::SeqCst) {
                return;
            }

            let elapsed = run_started.elapsed();

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
                resolution_bg.mark_solved(elapsed);
                return;
            }
        }
    });

    // The session is ready; attaching is the start of player interaction.
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

    let terminal_elapsed = run_started.elapsed();
    let used = hints_used.load(Ordering::SeqCst);

    // Pull the transcript out before ComposeGuard tears the container down.
    let transcript = save_transcript(&workstation, &scenario.meta.id);

    // The player may fix the issue and exit the shell inside the poller's 2s
    // window - do one final check before classifying the run as abandoned.
    if !resolution.is_solved()
        && !timed_out.load(Ordering::SeqCst)
        && check_success(
            &scenario.meta.success_condition,
            &scenario.meta.success_target,
            &scenario.check_script(),
            &scenario.dir,
        )
    {
        resolution.mark_solved(run_started.elapsed());
    }

    // compose stack torn down by ComposeGuard::drop, state file by StateFile::drop

    if resolution.is_solved() {
        return Ok(RunResult::Success {
            elapsed: resolution.elapsed_or(terminal_elapsed),
            hints_used: used,
            transcript,
        });
    }
    if timed_out.load(Ordering::SeqCst) {
        return Ok(RunResult::Timeout {
            hints_used: used,
            transcript,
        });
    }
    Ok(RunResult::Abandoned { transcript })
}

/// Copy the tmux pipe-pane capture out of the workstation container into
/// the local transcripts directory. Returns None (never errors) if there is
/// nothing to save - recording is strictly best-effort.
fn save_transcript(workstation: &str, scenario_id: &str) -> Option<PathBuf> {
    let dir = crate::recorder::transcripts_dir();
    fs::create_dir_all(&dir).ok()?;
    let name = format!(
        "{scenario_id}-{}.log",
        chrono::Utc::now().format("%Y%m%dT%H%M%SZ")
    );
    let dest = dir.join(name);
    let status = Command::new("docker")
        .args(["cp", &format!("{workstation}:{TRANSCRIPT_PATH}")])
        .arg(&dest)
        .status()
        .ok()?;
    if !status.success() || !dest.exists() {
        return None;
    }
    Some(dest)
}

/// Test a scenario end-to-end without a player:
/// up -> break -> assert the check fails -> solve -> assert the check passes.
/// Scenarios with a faults list get one full cycle per fault; `requested`
/// narrows to a single named fault.
pub fn test_scenario(scenario: &Scenario, requested: Option<&str>) -> Result<()> {
    if scenario.meta.faults.is_empty() || requested.is_some() {
        let fault = scenario.select_fault(requested, 0)?;
        return test_fault(scenario, &fault);
    }
    let total = scenario.meta.faults.len();
    for (i, f) in scenario.meta.faults.iter().enumerate() {
        println!(
            "[replaybook] test: fault \"{}\" ({}/{total})",
            f.name,
            i + 1
        );
        let fault = scenario.select_fault(Some(&f.name), 0)?;
        test_fault(scenario, &fault)?;
    }
    Ok(())
}

fn test_fault(scenario: &Scenario, fault: &ActiveFault) -> Result<()> {
    if !fault.solve_script.exists() {
        bail!(
            "{} has no solve script at {} - replaybook test needs one to verify the fix path",
            scenario.meta.id,
            fault.solve_script.display()
        );
    }

    let state = StateFile::new(&scenario.meta.id)?;
    println!("[replaybook] test: starting environment...");
    compose_down(scenario);
    let _compose_guard = ComposeGuard { scenario };
    compose_up(scenario, &state)?;
    std::thread::sleep(Duration::from_secs(2));

    println!("[replaybook] test: injecting fault...");
    inject_fault(scenario, fault)?;

    // Give the fault a moment to take effect (processes die, checks settle).
    std::thread::sleep(FAULT_SETTLE);
    ensure_fault_active(scenario)?;
    println!("[replaybook] test: check fails while broken (good)");

    println!("[replaybook] test: applying solve script...");
    run_script(&fault.solve_script, &scenario.dir)?;

    let deadline = Instant::now() + Duration::from_secs(90);
    loop {
        if scenario_check(scenario) {
            break;
        }
        if Instant::now() >= deadline {
            bail!(
                "check still failing 90s after the solve script - the solve is wrong or the check cannot detect recovery"
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

fn ensure_fault_active(scenario: &Scenario) -> Result<()> {
    fault_preflight_result(scenario_check(scenario))
}

fn fault_preflight_result(check_passes: bool) -> Result<()> {
    if check_passes {
        bail!(
            "check passes while the fault is applied - the fault didn't take or the check is wrong"
        );
    }
    Ok(())
}

fn inject_fault(scenario: &Scenario, fault: &ActiveFault) -> Result<()> {
    match &fault.break_steps {
        Some(steps) => run_break_steps(scenario, steps),
        None => run_script(&fault.break_script, &scenario.dir),
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

fn setup_tmux(container: &str) -> Result<()> {
    if !docker_exec(
        container,
        &["tmux", "new-session", "-d", "-s", TMUX_SESSION],
    ) {
        bail!("failed to start tmux in the workstation container");
    }
    // Record everything the player types and sees in the shell pane.
    // Best-effort: a failed recording never blocks the run.
    docker_exec(
        container,
        &[
            "tmux",
            "pipe-pane",
            "-t",
            &format!("{TMUX_SESSION}:0.0"),
            "-o",
            &format!("cat >> {TRANSCRIPT_PATH}"),
        ],
    );
    if !docker_exec(
        container,
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
    if !docker_exec(
        container,
        &[
            "tmux",
            "select-pane",
            "-t",
            &format!("{}:0.0", TMUX_SESSION),
        ],
    ) {
        bail!("failed to select the workstation shell pane");
    }
    Ok(())
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
    let output = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["config", "--format", "json"])
        .output();
    match output {
        Ok(output) => parse_networks(&output.stdout),
        Err(_) => vec!["default".to_string()],
    }
}

/// Extract the logical network names from `docker compose config --format
/// json` output. Anything unparseable falls back to the implicit default
/// network rather than failing the run.
fn parse_networks(config_json: &[u8]) -> Vec<String> {
    let fallback = || vec!["default".to_string()];
    let Ok(config) = serde_json::from_slice::<serde_json::Value>(config_json) else {
        return fallback();
    };
    match config.get("networks").and_then(|n| n.as_object()) {
        Some(map) if !map.is_empty() => map.keys().cloned().collect(),
        _ => fallback(),
    }
}

/// The compose override that injects the workstation service: joined to
/// every scenario network, docker socket mounted, HUD state file mounted.
fn workstation_override(state_host_path: &Path, networks: &[String]) -> String {
    let host_path = state_host_path.display();
    let container_path = StateFile::container_path();
    let mut content = format!(
        "services:\n  {WORKSTATION_SERVICE}:\n    image: {WORKSTATION_IMAGE}\n    command: [\"sleep\", \"infinity\"]\n    working_dir: /root\n    volumes:\n      - {host_path}:{container_path}\n      - /var/run/docker.sock:/var/run/docker.sock\n    networks:\n"
    );
    for n in networks {
        content.push_str(&format!("      - {n}\n"));
    }
    content.push_str("networks:\n");
    for n in networks {
        content.push_str(&format!("  {n}: {{}}\n"));
    }
    content
}

/// Brings the stack up plus a workstation container joined to the scenario's
/// networks, with the docker CLI and the HUD state file mounted. Returns the
/// workstation's container ID.
fn compose_up(scenario: &Scenario, state: &StateFile) -> Result<String> {
    let override_file = override_path(scenario);
    let networks = compose_networks(scenario);
    let content = workstation_override(&state.host_path, &networks);
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
        transcript: Option<PathBuf>,
    },
    Timeout {
        hints_used: usize,
        transcript: Option<PathBuf>,
    },
    Abandoned {
        transcript: Option<PathBuf>,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_networks_reads_logical_names() {
        let json = br#"{"networks": {"internal": {"name": "001_internal"}, "edge": {}}}"#;
        let mut nets = parse_networks(json);
        nets.sort();
        assert_eq!(nets, vec!["edge", "internal"]);
    }

    #[test]
    fn parse_networks_falls_back_to_default() {
        // Missing key, empty map, and garbage all fall back rather than fail.
        assert_eq!(parse_networks(br#"{"services": {}}"#), vec!["default"]);
        assert_eq!(parse_networks(br#"{"networks": {}}"#), vec!["default"]);
        assert_eq!(parse_networks(b"not json at all"), vec!["default"]);
        assert_eq!(parse_networks(b""), vec!["default"]);
    }

    #[test]
    fn workstation_override_mounts_and_networks() {
        let state = Path::new("/tmp/on-call-state-x");
        let networks = vec!["internal".to_string(), "default".to_string()];
        let yaml = workstation_override(state, &networks);

        assert!(yaml.contains("replaybook-workstation:"));
        assert!(yaml.contains("- /tmp/on-call-state-x:/tmp/on-call-state"));
        assert!(yaml.contains("- /var/run/docker.sock:/var/run/docker.sock"));
        // Service is attached to each network...
        assert!(yaml.contains("      - internal\n"));
        assert!(yaml.contains("      - default\n"));
        // ...and each network is declared at the top level so the override
        // merges whether or not the main file declares it.
        assert!(yaml.contains("  internal: {}\n"));
        assert!(yaml.contains("  default: {}\n"));
    }

    #[test]
    fn state_file_format_matches_hud_line_contract() {
        // The HUD shell script reads this file with `sed -n '1p'..'4p'` and
        // `tail -n +5`; this test pins that line layout.
        let state = StateFile::new("test-contract").unwrap();
        state.write(
            "ACTIVE",
            125,
            1,
            2,
            &["first hint".to_string(), "second hint".to_string()],
        );
        let content = fs::read_to_string(&state.host_path).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(
            lines,
            vec!["ACTIVE", "125", "1", "2", "first hint", "second hint"]
        );
    }

    #[test]
    fn state_file_removed_on_drop() {
        let path = {
            let state = StateFile::new("test-drop").unwrap();
            assert!(state.host_path.exists());
            state.host_path.clone()
        };
        assert!(!path.exists());
    }

    #[test]
    fn success_elapsed_uses_oracle_time_not_terminal_exit() {
        let resolution = Resolution::default();
        resolution.mark_solved(Duration::from_millis(1_250));

        assert_eq!(
            resolution.elapsed_or(Duration::from_secs(90)),
            Duration::from_millis(1_250)
        );
    }

    #[test]
    fn success_elapsed_preserves_first_success() {
        let resolution = Resolution::default();
        resolution.mark_solved(Duration::from_secs(4));
        resolution.mark_solved(Duration::from_secs(9));

        assert_eq!(
            resolution.elapsed_or(Duration::from_secs(20)),
            Duration::from_secs(4)
        );
    }

    #[test]
    fn fault_preflight_requires_oracle_to_fail() {
        assert!(fault_preflight_result(false).is_ok());

        let err = fault_preflight_result(true).unwrap_err().to_string();
        assert!(err.contains("check passes while the fault is applied"));
    }
}
