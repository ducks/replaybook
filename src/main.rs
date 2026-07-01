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
    /// Run a scenario by ID
    Run {
        /// Scenario ID (e.g. 001-nginx-502)
        id: String,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
        /// SLA time limit in minutes
        #[arg(long, default_value_t = 15)]
        sla: u64,
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
            scenarios_dir,
            sla,
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
                anyhow::bail!("cannot run an invalid scenario");
            }

            println!("[replaybook] scenario: {}", scenario.meta.title);

            let result = runner::run_scenario(scenario, sla * 60).await?;

            match result {
                RunResult::Success {
                    elapsed,
                    hints_used,
                } => {
                    println!(
                        "\n✓ resolved in {}s ({} hints used)",
                        elapsed.as_secs(),
                        hints_used
                    );
                    recorder::record(
                        &scenario.meta.id,
                        recorder::Outcome::Success,
                        Some(elapsed),
                        hints_used as u8,
                    )?;
                }
                RunResult::Timeout { hints_used } => {
                    println!("\n✗ SLA breached ({} hints used).", hints_used);
                    recorder::record(
                        &scenario.meta.id,
                        recorder::Outcome::Timeout,
                        None,
                        hints_used as u8,
                    )?;
                }
                RunResult::Abandoned => {
                    println!("\nShell exited before resolution. Run again to retry.");
                    recorder::record(&scenario.meta.id, recorder::Outcome::Abandoned, None, 0)?;
                }
            }
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
