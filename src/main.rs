mod recorder;
mod runner;
mod scenario;
mod validate;

use anyhow::Result;
use clap::{Parser, Subcommand};
use runner::RunResult;
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "replaybook",
    about = "Incident replay trainer. Fix broken infrastructure to win."
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Add a scenario pack from a GitHub repo (e.g. ducks/replaybook-scenarios)
    Add {
        /// GitHub repo in owner/repo format
        repo: String,
    },
    /// List available scenarios
    List {
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
    },
    /// Run a scenario by ID, or a random one with --random
    Run {
        /// Scenario ID (e.g. 001-nginx-502)
        #[arg(required_unless_present = "random", conflicts_with = "random")]
        id: Option<String>,
        /// Pick a random scenario instead of naming one
        #[arg(long)]
        random: bool,
        /// With --random, only consider scenarios carrying this tag
        #[arg(long)]
        tag: Option<String>,
        /// Force a named fault variant (defaults to a random one)
        #[arg(long)]
        fault: Option<String>,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
        /// SLA time limit in minutes
        #[arg(long, default_value_t = 15)]
        sla: u64,
    },
    /// Test a scenario end-to-end: break, assert broken, solve, assert solved
    Test {
        /// Scenario ID (e.g. 001-nginx-502)
        id: String,
        /// Test only this fault variant (defaults to all of them)
        #[arg(long)]
        fault: Option<String>,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
    },
    /// Export session records as JSONL
    Export,
}

fn default_scenarios_dir() -> PathBuf {
    dirs_next::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join("replaybook/scenarios")
}

fn resolve_scenarios_dir(arg: Option<PathBuf>) -> PathBuf {
    arg.unwrap_or_else(default_scenarios_dir)
}

fn no_scenarios_found() {
    println!("No scenarios found.");
    println!("Add a scenario pack with: replaybook add ducks/replaybook-scenarios");
}

fn print_transcript(transcript: &Option<PathBuf>) {
    if let Some(path) = transcript {
        println!("  transcript: {}", path.display());
    }
}

fn print_fault(fault: Option<&str>) {
    if let Some(name) = fault {
        println!("  fault: {name}");
    }
}

/// Arbitrary entropy for scenario/fault picks - game randomness, not crypto.
fn seed() -> usize {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos() as usize)
        .unwrap_or(0)
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Add { repo } => {
            let url = format!("https://github.com/{}.git", repo);
            let pack_name = repo.split('/').next_back().unwrap_or(&repo);
            let dest = default_scenarios_dir().join(pack_name);

            if dest.exists() {
                println!("[replaybook] updating {}...", repo);
                let status = std::process::Command::new("git")
                    .args(["pull", "--ff-only"])
                    .current_dir(&dest)
                    .status()?;
                if !status.success() {
                    anyhow::bail!("git pull failed");
                }
            } else {
                println!("[replaybook] adding {}...", repo);
                std::fs::create_dir_all(dest.parent().unwrap())?;
                let status = std::process::Command::new("git")
                    .args(["clone", "--depth=1", &url, dest.to_str().unwrap()])
                    .status()?;
                if !status.success() {
                    anyhow::bail!("git clone failed");
                }
            }

            let scenarios = scenario::discover(&dest)?;
            let mut broken = 0;
            for s in &scenarios {
                let issues = validate::validate(s)?;
                if !issues.is_empty() {
                    broken += 1;
                    eprintln!("[replaybook] {} failed validation:", s.meta.id);
                    for issue in issues {
                        eprintln!("  - {}", issue.message);
                    }
                }
            }

            if broken > 0 {
                println!(
                    "[replaybook] done. {broken} scenario(s) failed validation - see above. replaybook run will re-check before launching."
                );
            } else {
                println!("[replaybook] done. run 'replaybook list' to see available scenarios.");
            }
        }

        Commands::List { scenarios_dir } => {
            let dir = resolve_scenarios_dir(scenarios_dir);
            if !dir.exists() {
                no_scenarios_found();
                return Ok(());
            }
            let scenarios = scenario::discover(&dir)?;
            if scenarios.is_empty() {
                no_scenarios_found();
                return Ok(());
            }
            println!("{:<30} {:<5} TITLE", "ID", "DIFF");
            println!("{}", "─".repeat(60));
            for s in scenarios {
                println!(
                    "{:<30} {:<5} {}",
                    s.meta.id, s.meta.difficulty, s.meta.title
                );
            }
        }

        Commands::Run {
            id,
            random,
            tag,
            fault,
            scenarios_dir,
            sla,
        } => {
            // clap can't express "--tag needs --random" against a bool flag
            // (default values satisfy `requires`), so enforce it here.
            if tag.is_some() && !random {
                anyhow::bail!("--tag only applies with --random");
            }
            let dir = resolve_scenarios_dir(scenarios_dir);
            if !dir.exists() {
                no_scenarios_found();
                return Ok(());
            }
            let scenarios = scenario::discover(&dir)?;
            let scenario = match (&id, random) {
                (Some(id), _) => scenarios
                    .iter()
                    .find(|s| s.meta.id == *id)
                    .ok_or_else(|| anyhow::anyhow!("scenario '{}' not found", id))?,
                (None, _) => {
                    let pool: Vec<_> = scenarios
                        .iter()
                        .filter(|s| {
                            tag.as_ref()
                                .is_none_or(|t| s.meta.tags.iter().any(|st| st == t))
                        })
                        .collect();
                    if pool.is_empty() {
                        match &tag {
                            Some(t) => anyhow::bail!("no scenarios tagged \"{t}\""),
                            None => {
                                no_scenarios_found();
                                return Ok(());
                            }
                        }
                    }
                    pool[seed() % pool.len()]
                }
            };

            let issues = validate::validate(scenario)?;
            if !issues.is_empty() {
                eprintln!(
                    "[replaybook] scenario '{}' failed validation:",
                    scenario.meta.id
                );
                for issue in &issues {
                    eprintln!("  - {}", issue.message);
                }
                anyhow::bail!("cannot run an invalid scenario");
            }

            // Resolved before the run but only revealed after - the fault
            // name would spoil the diagnosis.
            let active = scenario.select_fault(fault.as_deref(), seed())?;

            println!("[replaybook] scenario: {}", scenario.meta.title);

            let result = runner::run_scenario(scenario, &active, sla * 60).await?;

            let fault_name = active.name.as_deref();
            match result {
                RunResult::Success {
                    elapsed,
                    hints_used,
                    transcript,
                } => {
                    println!(
                        "\n✓ resolved in {}s ({} hints used)",
                        elapsed.as_secs(),
                        hints_used
                    );
                    print_fault(fault_name);
                    print_transcript(&transcript);
                    recorder::record(
                        &scenario.meta.id,
                        recorder::Outcome::Success,
                        Some(elapsed),
                        hints_used as u8,
                        transcript.as_deref(),
                        fault_name,
                    )?;
                }
                RunResult::Timeout {
                    hints_used,
                    transcript,
                } => {
                    println!("\n✗ SLA breached ({} hints used).", hints_used);
                    print_fault(fault_name);
                    print_transcript(&transcript);
                    recorder::record(
                        &scenario.meta.id,
                        recorder::Outcome::Timeout,
                        None,
                        hints_used as u8,
                        transcript.as_deref(),
                        fault_name,
                    )?;
                }
                RunResult::Abandoned { transcript } => {
                    println!("\nShell exited before resolution. Run again to retry.");
                    print_fault(fault_name);
                    print_transcript(&transcript);
                    recorder::record(
                        &scenario.meta.id,
                        recorder::Outcome::Abandoned,
                        None,
                        0,
                        transcript.as_deref(),
                        fault_name,
                    )?;
                }
            }
        }

        Commands::Test {
            id,
            fault,
            scenarios_dir,
        } => {
            let dir = resolve_scenarios_dir(scenarios_dir);
            if !dir.exists() {
                no_scenarios_found();
                return Ok(());
            }
            let scenarios = scenario::discover(&dir)?;
            let scenario = scenarios
                .iter()
                .find(|s| s.meta.id == id)
                .ok_or_else(|| anyhow::anyhow!("scenario '{}' not found", id))?;

            let issues = validate::validate(scenario)?;
            if !issues.is_empty() {
                eprintln!("[replaybook] scenario '{}' failed validation:", id);
                for issue in &issues {
                    eprintln!("  - {}", issue.message);
                }
                anyhow::bail!("cannot test an invalid scenario");
            }

            println!("[replaybook] testing: {}", scenario.meta.title);
            runner::test_scenario(scenario, fault.as_deref())?;
            println!("\n✓ {} passed", scenario.meta.id);
        }

        Commands::Export => {
            let path = recorder::sessions_dir().join("sessions.jsonl");

            if !path.exists() {
                println!("No sessions recorded yet.");
                return Ok(());
            }

            print!("{}", std::fs::read_to_string(&path)?);
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_defaults_to_15_minute_sla() {
        let cli = Cli::try_parse_from(["replaybook", "run", "001-nginx-502"]).unwrap();
        match cli.command {
            Commands::Run { id, sla, fault, .. } => {
                assert_eq!(id.as_deref(), Some("001-nginx-502"));
                assert_eq!(sla, 15);
                assert_eq!(fault, None);
            }
            _ => panic!("expected run"),
        }
    }

    #[test]
    fn run_and_test_accept_scenarios_dir() {
        let cli = Cli::try_parse_from([
            "replaybook",
            "test",
            "002-postgres-rejecting-connections",
            "--scenarios-dir",
            "/tmp/packs",
        ])
        .unwrap();
        match cli.command {
            Commands::Test {
                id,
                fault,
                scenarios_dir,
            } => {
                assert_eq!(id, "002-postgres-rejecting-connections");
                assert_eq!(fault, None);
                assert_eq!(scenarios_dir, Some(PathBuf::from("/tmp/packs")));
            }
            _ => panic!("expected test"),
        }
    }

    #[test]
    fn unknown_subcommand_is_rejected() {
        assert!(Cli::try_parse_from(["replaybook", "conquer"]).is_err());
        assert!(Cli::try_parse_from(["replaybook", "run"]).is_err()); // id or --random required
    }

    #[test]
    fn run_random_replaces_id_and_gates_tag() {
        let cli =
            Cli::try_parse_from(["replaybook", "run", "--random", "--tag", "postgres"]).unwrap();
        match cli.command {
            Commands::Run {
                id, random, tag, ..
            } => {
                assert_eq!(id, None);
                assert!(random);
                assert_eq!(tag.as_deref(), Some("postgres"));
            }
            _ => panic!("expected run"),
        }
        // id and --random conflict at parse time; --tag-without---random is
        // rejected at runtime (clap default values defeat `requires`).
        assert!(Cli::try_parse_from(["replaybook", "run", "x", "--random"]).is_err());
    }

    #[test]
    fn run_and_test_accept_fault() {
        let cli =
            Cli::try_parse_from(["replaybook", "run", "006-x", "--fault", "redis-auth"]).unwrap();
        match cli.command {
            Commands::Run { fault, .. } => assert_eq!(fault.as_deref(), Some("redis-auth")),
            _ => panic!("expected run"),
        }
        let cli =
            Cli::try_parse_from(["replaybook", "test", "006-x", "--fault", "redis-auth"]).unwrap();
        match cli.command {
            Commands::Test { fault, .. } => assert_eq!(fault.as_deref(), Some("redis-auth")),
            _ => panic!("expected test"),
        }
    }
}
