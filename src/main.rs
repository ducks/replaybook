mod author;
mod backend;
mod control;
mod hosted;
mod recorder;
mod runner;
mod scenario;
mod validate;

use anyhow::Result;
use backend::{ExecutionBackend, LocalDockerBackend, RemoteVmBackend};
use clap::{Parser, Subcommand};
use runner::RunResult;
use std::net::SocketAddr;
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "replaybook",
    about = "Incident replay trainer. Fix broken infrastructure to win.",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Create a runnable starter scenario in a pack directory
    New {
        /// Scenario ID (lowercase letters, digits, and hyphens)
        id: String,
        /// Directory that will contain the new scenario
        #[arg(long, default_value = ".")]
        pack: PathBuf,
    },
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
    /// Run a scenario by ID or directory, or a random one with --random
    Run {
        /// Scenario ID or path (e.g. 001-nginx-502 or ./scenarios/001-nginx-502)
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
    /// Run a scenario on a dedicated remote VM and attach over SSH
    Remote {
        /// Scenario ID or local directory to stage
        target: String,
        /// Provisioning SSH destination in [user@]host form
        #[arg(long)]
        host: String,
        /// Provisioning and participant SSH port
        #[arg(long, default_value_t = 22)]
        ssh_port: u16,
        /// Force a named fault variant
        #[arg(long)]
        fault: Option<String>,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
        /// SLA time limit in minutes
        #[arg(long, default_value_t = 15)]
        sla: u64,
    },
    /// Serve the authenticated hosted-session control plane
    Serve {
        /// Dedicated VM reached through provisioning SSH
        #[arg(long)]
        host: String,
        #[arg(long, default_value_t = 22)]
        ssh_port: u16,
        /// HTTP listen address; use a TLS reverse proxy for non-loopback access
        #[arg(long, default_value = "127.0.0.1:8080")]
        bind: SocketAddr,
        /// Environment variable containing the bearer token
        #[arg(long, default_value = "REPLAYBOOK_CONTROL_TOKEN")]
        token_env: String,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
        #[arg(long, default_value_t = 60)]
        default_ttl: u64,
    },
    /// Validate a scenario without starting its environment
    Validate {
        /// Scenario ID or directory
        target: String,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
    },
    /// Test one scenario end-to-end, or every scenario in a pack with --all
    Test {
        /// Scenario ID/directory, or pack directory with --all
        target: Option<String>,
        /// Test every scenario in the target pack
        #[arg(long)]
        all: bool,
        /// Test only this fault variant (defaults to all of them)
        #[arg(long, conflicts_with = "all")]
        fault: Option<String>,
        #[arg(long)]
        scenarios_dir: Option<PathBuf>,
    },
    /// Export session records as JSONL
    Export,
    #[command(hide = true)]
    HostedRun {
        #[arg(long)]
        session: String,
        #[arg(long)]
        scenario: PathBuf,
        #[arg(long)]
        sla: u64,
        #[arg(long)]
        fault: Option<String>,
    },
    #[command(hide = true)]
    HostedStatus {
        #[arg(long)]
        session: String,
    },
    #[command(hide = true)]
    HostedReady {
        #[arg(long)]
        session: String,
    },
    #[command(hide = true)]
    HostedCleanup {
        #[arg(long)]
        session: String,
        #[arg(long)]
        scenario: PathBuf,
    },
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

fn resolve_scenario(target: &str, scenarios_dir: Option<PathBuf>) -> Result<scenario::Scenario> {
    let path = PathBuf::from(target);
    if path.is_dir() {
        return scenario::Scenario::load(&path);
    }
    if path.is_file() && path.file_name().is_some_and(|name| name == "meta.json") {
        return scenario::Scenario::load(
            path.parent().unwrap_or_else(|| std::path::Path::new(".")),
        );
    }
    if path.is_absolute() || target.starts_with('.') || target.contains(std::path::MAIN_SEPARATOR) {
        anyhow::bail!("scenario path '{}' does not exist", path.display());
    }

    let dir = resolve_scenarios_dir(scenarios_dir);
    if !dir.exists() {
        anyhow::bail!(
            "scenario directory {} does not exist; add a pack or pass a scenario path",
            dir.display()
        );
    }
    scenario::discover(&dir)?
        .into_iter()
        .find(|scenario| scenario.meta.id == target)
        .ok_or_else(|| anyhow::anyhow!("scenario '{}' not found", target))
}

fn print_validation(scenario: &scenario::Scenario, issues: &[validate::Issue]) {
    if issues.is_empty() {
        println!("✓ {} is valid", scenario.meta.id);
        return;
    }
    eprintln!(
        "[replaybook] scenario '{}' failed validation:",
        scenario.meta.id
    );
    for issue in issues {
        eprintln!("  - {}", issue.message);
    }
}

fn validate_scenario(scenario: &scenario::Scenario) -> Result<()> {
    let issues = validate::validate(scenario)?;
    print_validation(scenario, &issues);
    if !issues.is_empty() {
        anyhow::bail!("scenario validation failed");
    }
    Ok(())
}

async fn execute_run<B: ExecutionBackend>(
    backend: &B,
    scenario: &scenario::Scenario,
    requested_fault: Option<&str>,
    sla_minutes: u64,
    record_locally: bool,
) -> Result<()> {
    validate_scenario(scenario).map_err(|_| anyhow::anyhow!("cannot run an invalid scenario"))?;
    let active = scenario.select_fault(requested_fault, seed())?;
    println!("[replaybook] scenario: {}", scenario.meta.title);
    let result = backend
        .run(scenario, &active, checked_sla_seconds(sla_minutes)?)
        .await?;
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
            if record_locally {
                recorder::record(
                    &scenario.meta.id,
                    recorder::Outcome::Success,
                    Some(elapsed),
                    hints_used as u8,
                    transcript.as_deref(),
                    fault_name,
                )?;
            }
        }
        RunResult::Timeout {
            hints_used,
            transcript,
        } => {
            println!("\n✗ SLA breached ({} hints used).", hints_used);
            print_fault(fault_name);
            print_transcript(&transcript);
            if record_locally {
                recorder::record(
                    &scenario.meta.id,
                    recorder::Outcome::Timeout,
                    None,
                    hints_used as u8,
                    transcript.as_deref(),
                    fault_name,
                )?;
            }
        }
        RunResult::Abandoned { transcript } => {
            println!("\nShell exited before resolution. Run again to retry.");
            print_fault(fault_name);
            print_transcript(&transcript);
            if record_locally {
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
    Ok(())
}

fn checked_sla_seconds(minutes: u64) -> Result<u64> {
    if minutes == 0 {
        anyhow::bail!("SLA must be greater than zero");
    }
    minutes
        .checked_mul(60)
        .ok_or_else(|| anyhow::anyhow!("SLA is too large"))
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
        Commands::New { id, pack } => {
            let stdin = std::io::stdin();
            let mut input = stdin.lock();
            let stdout = std::io::stdout();
            let mut output = stdout.lock();
            author::create(&id, &pack, &mut input, &mut output)?;
        }
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
            let scenario = match (&id, random) {
                (Some(target), _) => resolve_scenario(target, scenarios_dir)?,
                (None, _) => {
                    let dir = resolve_scenarios_dir(scenarios_dir);
                    if !dir.exists() {
                        no_scenarios_found();
                        return Ok(());
                    }
                    let scenarios = scenario::discover(&dir)?;
                    let pool: Vec<_> = scenarios
                        .into_iter()
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
                    let index = seed() % pool.len();
                    pool.into_iter().nth(index).unwrap()
                }
            };

            execute_run(&LocalDockerBackend, &scenario, fault.as_deref(), sla, true).await?;
        }

        Commands::Remote {
            target,
            host,
            ssh_port,
            fault,
            scenarios_dir,
            sla,
        } => {
            let scenario = resolve_scenario(&target, scenarios_dir)?;
            let backend = RemoteVmBackend::new(host, ssh_port)?;
            execute_run(&backend, &scenario, fault.as_deref(), sla, false).await?;
        }

        Commands::Serve {
            host,
            ssh_port,
            bind,
            token_env,
            scenarios_dir,
            default_ttl,
        } => {
            let token = std::env::var(&token_env).map_err(|_| {
                anyhow::anyhow!("environment variable {token_env} must contain the bearer token")
            })?;
            control::serve(control::ControlConfig {
                bind,
                token,
                scenarios_dir: resolve_scenarios_dir(scenarios_dir),
                backend: RemoteVmBackend::new(host, ssh_port)?,
                default_ttl_minutes: default_ttl,
            })
            .await?;
        }

        Commands::Validate {
            target,
            scenarios_dir,
        } => {
            let scenario = resolve_scenario(&target, scenarios_dir)?;
            validate_scenario(&scenario)?;
        }

        Commands::Test {
            target,
            all,
            fault,
            scenarios_dir,
        } => {
            if all {
                let dir = target
                    .map(PathBuf::from)
                    .unwrap_or_else(|| resolve_scenarios_dir(scenarios_dir));
                if !dir.exists() {
                    anyhow::bail!("scenario pack path '{}' does not exist", dir.display());
                }
                let scenarios = scenario::discover_strict(&dir)?;
                if scenarios.is_empty() {
                    anyhow::bail!("no scenarios found in {}", dir.display());
                }
                println!(
                    "[replaybook] testing {} scenario(s) from {}",
                    scenarios.len(),
                    dir.display()
                );
                let mut invalid = 0;
                for scenario in &scenarios {
                    let issues = validate::validate(scenario)?;
                    if !issues.is_empty() {
                        invalid += 1;
                        print_validation(scenario, &issues);
                    }
                }
                if invalid > 0 {
                    anyhow::bail!("{invalid} scenario(s) failed validation; no tests were run");
                }
                for (index, scenario) in scenarios.iter().enumerate() {
                    println!(
                        "\n[replaybook] ({}/{}) {}",
                        index + 1,
                        scenarios.len(),
                        scenario.meta.id
                    );
                    runner::test_scenario(scenario, None)?;
                    println!("✓ {} passed", scenario.meta.id);
                }
                println!("\n✓ all {} scenarios passed", scenarios.len());
            } else {
                let target = target.ok_or_else(|| {
                    anyhow::anyhow!("provide a scenario ID or path, or use --all [PACK]")
                })?;
                let scenario = resolve_scenario(&target, scenarios_dir)?;
                validate_scenario(&scenario)
                    .map_err(|_| anyhow::anyhow!("cannot test an invalid scenario"))?;
                println!("[replaybook] testing: {}", scenario.meta.title);
                runner::test_scenario(&scenario, fault.as_deref())?;
                println!("\n✓ {} passed", scenario.meta.id);
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

        Commands::HostedRun {
            session,
            scenario,
            sla,
            fault,
        } => hosted::run(&session, &scenario, sla, fault.as_deref()).await?,

        Commands::HostedStatus { session } => {
            println!("{}", serde_json::to_string(&hosted::status(&session)?)?);
        }

        Commands::HostedReady { session } => hosted::ready(&session)?,

        Commands::HostedCleanup { session, scenario } => {
            hosted::cleanup(&session, &scenario)?;
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

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
    fn cli_reports_the_package_version() {
        assert_eq!(
            Cli::command().get_version(),
            Some(env!("CARGO_PKG_VERSION"))
        );
    }

    #[test]
    fn sla_conversion_rejects_zero_and_overflow() {
        assert_eq!(checked_sla_seconds(15).unwrap(), 900);
        assert!(checked_sla_seconds(0).is_err());
        assert!(checked_sla_seconds(u64::MAX).is_err());
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
                target,
                all,
                fault,
                scenarios_dir,
            } => {
                assert_eq!(
                    target.as_deref(),
                    Some("002-postgres-rejecting-connections")
                );
                assert!(!all);
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

    #[test]
    fn authoring_commands_accept_paths() {
        let cli = Cli::try_parse_from([
            "replaybook",
            "new",
            "010-checkout-down",
            "--pack",
            "./incidents",
        ])
        .unwrap();
        match cli.command {
            Commands::New { id, pack } => {
                assert_eq!(id, "010-checkout-down");
                assert_eq!(pack, PathBuf::from("./incidents"));
            }
            _ => panic!("expected new"),
        }

        let cli = Cli::try_parse_from(["replaybook", "validate", "./incidents/010-x"]).unwrap();
        match cli.command {
            Commands::Validate { target, .. } => assert_eq!(target, "./incidents/010-x"),
            _ => panic!("expected validate"),
        }
    }

    #[test]
    fn test_all_accepts_an_optional_pack_and_rejects_fault() {
        let cli =
            Cli::try_parse_from(["replaybook", "test", "--all", "./company-incidents"]).unwrap();
        match cli.command {
            Commands::Test {
                target, all, fault, ..
            } => {
                assert_eq!(target.as_deref(), Some("./company-incidents"));
                assert!(all);
                assert!(fault.is_none());
            }
            _ => panic!("expected test"),
        }
        assert!(Cli::try_parse_from(["replaybook", "test", "--all", "--fault", "x"]).is_err());
    }

    #[test]
    fn remote_command_requires_a_host_and_defaults_ssh_and_sla() {
        let cli = Cli::try_parse_from([
            "replaybook",
            "remote",
            "001-nginx-502",
            "--host",
            "replay@training.example.com",
        ])
        .unwrap();
        match cli.command {
            Commands::Remote {
                target,
                host,
                ssh_port,
                sla,
                ..
            } => {
                assert_eq!(target, "001-nginx-502");
                assert_eq!(host, "replay@training.example.com");
                assert_eq!(ssh_port, 22);
                assert_eq!(sla, 15);
            }
            _ => panic!("expected remote"),
        }
        assert!(Cli::try_parse_from(["replaybook", "remote", "001-x"]).is_err());
    }

    #[test]
    fn serve_defaults_to_loopback_and_token_environment() {
        let cli = Cli::try_parse_from([
            "replaybook",
            "serve",
            "--host",
            "replay@training.example.com",
        ])
        .unwrap();
        match cli.command {
            Commands::Serve {
                bind,
                token_env,
                default_ttl,
                ..
            } => {
                assert_eq!(bind, "127.0.0.1:8080".parse::<SocketAddr>().unwrap());
                assert_eq!(token_env, "REPLAYBOOK_CONTROL_TOKEN");
                assert_eq!(default_ttl, 60);
            }
            _ => panic!("expected serve"),
        }
    }

    #[test]
    fn hosted_commands_parse_but_are_hidden_from_help() {
        let id = "b4fd7a54-72a8-42a5-919f-d8b6fbdff8b0";
        assert!(
            Cli::try_parse_from([
                "replaybook",
                "hosted-run",
                "--session",
                id,
                "--scenario",
                "/tmp/replaybook-hosted/b4fd7a54-72a8-42a5-919f-d8b6fbdff8b0/scenario",
                "--sla",
                "15",
            ])
            .is_ok()
        );
        let mut help = Vec::new();
        Cli::command().write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap();
        assert!(!help.contains("hosted-run"));
        assert!(!help.contains("hosted-cleanup"));
    }
}
