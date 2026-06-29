mod recorder;
mod runner;
mod scenario;

use anyhow::Result;
use clap::{Parser, Subcommand};
use runner::RunResult;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "on-call", about = "Fix broken infrastructure to win.")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// List available scenarios
    List {
        #[arg(long, default_value = "scenarios")]
        scenarios_dir: PathBuf,
    },
    /// Run a scenario by ID
    Run {
        /// Scenario ID (e.g. 001-nginx-502)
        id: String,
        #[arg(long, default_value = "scenarios")]
        scenarios_dir: PathBuf,
        /// SLA time limit in minutes
        #[arg(long, default_value_t = 15)]
        sla: u64,
    },
    /// Export session records as JSONL
    Export,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::List { scenarios_dir } => {
            let scenarios = scenario::discover(&scenarios_dir)?;
            if scenarios.is_empty() {
                println!("No scenarios found in {}", scenarios_dir.display());
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
            let scenarios = scenario::discover(&scenarios_dir)?;
            let scenario = scenarios
                .iter()
                .find(|s| s.meta.id == id)
                .ok_or_else(|| anyhow::anyhow!("scenario '{}' not found", id))?;

            println!("[on-call] scenario: {}", scenario.meta.title);

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
            let path = std::env::var("HOME")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|_| std::path::PathBuf::from("/tmp"))
                .join(".local/share/on-call/sessions/sessions.jsonl");

            if !path.exists() {
                println!("No sessions recorded yet.");
                return Ok(());
            }

            print!("{}", std::fs::read_to_string(&path)?);
        }
    }

    Ok(())
}
