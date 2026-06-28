use crate::scenario::{Scenario, SuccessCondition};
use anyhow::{bail, Result};
use std::process::Command;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::time::{Duration, Instant};

pub async fn run_scenario(scenario: &Scenario, sla_seconds: u64) -> Result<RunResult> {
    println!("\n[on-call] starting environment...");
    compose_up(scenario)?;

    println!("[on-call] injecting fault...");
    run_script(scenario.break_script())?;

    // Find the primary container to exec into
    let container = primary_container(scenario)?;

    let hr: String = "─".repeat(60);
    println!("\n{hr}");
    println!("PAGE: {}", scenario.meta.page);
    println!("{hr}");
    println!("SLA: {} minutes\n", sla_seconds / 60);

    // Poll for success in a background task, signal when done
    let solved = Arc::new(AtomicBool::new(false));
    let timed_out = Arc::new(AtomicBool::new(false));
    let solved_bg = solved.clone();
    let timed_out_bg = timed_out.clone();
    let scenario_dir = scenario.dir.clone();
    let check_script = scenario.check_script();
    let success_condition = scenario.meta.success_condition.clone();
    let success_target = scenario.meta.success_target.clone();
    let deadline = Duration::from_secs(sla_seconds);

    let poller = tokio::task::spawn_blocking(move || {
        let started = Instant::now();
        loop {
            std::thread::sleep(Duration::from_secs(5));

            if started.elapsed() >= deadline {
                timed_out_bg.store(true, Ordering::SeqCst);
                return;
            }

            let ok = match success_condition {
                SuccessCondition::Http200 => {
                    Command::new("curl")
                        .args(["-sf", "--max-time", "4", &success_target])
                        .status()
                        .map(|s| s.success())
                        .unwrap_or(false)
                }
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

            if ok {
                solved_bg.store(true, Ordering::SeqCst);
                return;
            }
        }
    });

    // Drop the player into the container
    let started = Instant::now();
    Command::new("docker")
        .args(["exec", "-it", &container, "sh", "-c",
               "[ -x /bin/bash ] && exec /bin/bash || exec /bin/sh"])
        .status()
        .ok();

    // Shell exited - check outcome
    poller.abort();

    if solved.load(Ordering::SeqCst) {
        let elapsed = started.elapsed();
        compose_down(scenario)?;
        return Ok(RunResult::Success { elapsed });
    }

    if timed_out.load(Ordering::SeqCst) {
        compose_down(scenario)?;
        return Ok(RunResult::Timeout);
    }

    // Player exited the shell manually before solving
    compose_down(scenario)?;
    Ok(RunResult::Abandoned)
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

    // Use the container named after the primary service if specified,
    // otherwise just take the first one
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

fn compose_up(scenario: &Scenario) -> Result<()> {
    let status = Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["up", "-d", "--build"])
        .status()?;
    if !status.success() {
        bail!("docker compose up failed");
    }
    Ok(())
}

fn compose_down(scenario: &Scenario) -> Result<()> {
    Command::new("docker")
        .args(["compose", "-f"])
        .arg(scenario.compose_file())
        .args(["down", "-v", "--remove-orphans"])
        .status()
        .ok();
    Ok(())
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
    Success { elapsed: Duration },
    Timeout,
    Abandoned,
}
