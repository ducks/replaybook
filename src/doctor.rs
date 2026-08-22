use serde::Serialize;
use std::env;
use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;

const HOST_DISK_PASS_GIB: u64 = 40;
const HOST_DISK_WARN_GIB: u64 = 10;
const HOST_MEMORY_PASS_GIB: u64 = 8;
const HOST_MEMORY_WARN_GIB: u64 = 4;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Pass,
    Warn,
    Fail,
    Info,
}

impl Status {
    fn marker(self) -> &'static str {
        match self {
            Self::Pass => "PASS",
            Self::Warn => "WARN",
            Self::Fail => "FAIL",
            Self::Info => "INFO",
        }
    }
}

#[derive(Debug, Serialize)]
pub struct Check {
    pub group: &'static str,
    pub name: &'static str,
    pub status: Status,
    pub detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remedy: Option<String>,
}

impl Check {
    fn new(
        group: &'static str,
        name: &'static str,
        status: Status,
        detail: impl Into<String>,
        remedy: Option<&str>,
    ) -> Self {
        Self {
            group,
            name,
            status,
            detail: detail.into(),
            remedy: remedy.map(str::to_owned),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct Summary {
    pub passed: usize,
    pub warnings: usize,
    pub failed: usize,
    pub informational: usize,
}

#[derive(Debug, Serialize)]
pub struct Report {
    pub schema_version: u8,
    pub replaybook_version: &'static str,
    pub host_benchmark: bool,
    pub checks: Vec<Check>,
    pub summary: Summary,
}

impl Report {
    pub fn healthy(&self) -> bool {
        self.summary.failed == 0
    }
}

#[derive(Debug)]
pub struct Config {
    pub host: bool,
    pub base_port: u16,
    pub concurrency: u16,
    pub scenarios_dir: PathBuf,
}

trait Probe {
    fn command_output(&self, program: &str, args: &[&str]) -> Option<String>;
    fn executable(&self, program: &str) -> Option<PathBuf>;
    fn path_exists(&self, path: &Path) -> bool;
    fn path_read_write(&self, path: &Path) -> bool;
    fn read_to_string(&self, path: &Path) -> Option<String>;
    fn env_var(&self, name: &str) -> Option<String>;
    fn ports_available(&self, ports: &[u16]) -> Result<(), String>;
}

struct SystemProbe;

impl Probe for SystemProbe {
    fn command_output(&self, program: &str, args: &[&str]) -> Option<String> {
        let output = Command::new(program).args(args).output().ok()?;
        if !output.status.success() {
            return None;
        }
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        Some(if stdout.is_empty() { stderr } else { stdout })
    }

    fn executable(&self, program: &str) -> Option<PathBuf> {
        let path = env::var_os("PATH")?;
        env::split_paths(&path)
            .map(|directory| directory.join(program))
            .find(|candidate| candidate.is_file())
    }

    fn path_exists(&self, path: &Path) -> bool {
        path.exists()
    }

    fn path_read_write(&self, path: &Path) -> bool {
        fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(path)
            .is_ok()
    }

    fn read_to_string(&self, path: &Path) -> Option<String> {
        fs::read_to_string(path).ok()
    }

    fn env_var(&self, name: &str) -> Option<String> {
        env::var(name).ok().filter(|value| !value.is_empty())
    }

    fn ports_available(&self, ports: &[u16]) -> Result<(), String> {
        let mut listeners = Vec::with_capacity(ports.len());
        for port in ports {
            let listener = TcpListener::bind(("127.0.0.1", *port))
                .map_err(|error| format!("127.0.0.1:{port}: {error}"))?;
            listeners.push(listener);
        }
        Ok(())
    }
}

pub fn inspect(config: &Config) -> Report {
    inspect_with(config, &SystemProbe)
}

fn inspect_with(config: &Config, probe: &impl Probe) -> Report {
    let mut checks = Vec::new();
    command_check(
        probe,
        &mut checks,
        "core",
        "docker-cli",
        ("docker", &["--version"]),
        Status::Fail,
        "Install Docker and ensure `docker` is on PATH.",
    );
    command_check(
        probe,
        &mut checks,
        "core",
        "docker-compose",
        ("docker", &["compose", "version"]),
        Status::Fail,
        "Install the Docker Compose plugin.",
    );
    command_check(
        probe,
        &mut checks,
        "core",
        "docker-daemon",
        ("docker", &["info", "--format", "{{.ServerVersion}}"]),
        Status::Fail,
        "Start Docker and ensure your user can access its socket.",
    );
    command_check(
        probe,
        &mut checks,
        "core",
        "git",
        ("git", &["--version"]),
        Status::Warn,
        "Install Git to add and update scenario packs.",
    );

    if probe.path_exists(&config.scenarios_dir) {
        checks.push(Check::new(
            "core",
            "scenario-pack",
            Status::Pass,
            config.scenarios_dir.display().to_string(),
            None,
        ));
    } else {
        checks.push(Check::new(
            "core",
            "scenario-pack",
            Status::Warn,
            format!("not found at {}", config.scenarios_dir.display()),
            Some("Run `replaybook add ducks/replaybook-scenarios`."),
        ));
    }

    if config.host {
        host_checks(config, probe, &mut checks);
    }

    let summary = Summary {
        passed: count(&checks, Status::Pass),
        warnings: count(&checks, Status::Warn),
        failed: count(&checks, Status::Fail),
        informational: count(&checks, Status::Info),
    };
    Report {
        schema_version: 1,
        replaybook_version: env!("CARGO_PKG_VERSION"),
        host_benchmark: config.host,
        checks,
        summary,
    }
}

fn command_check(
    probe: &impl Probe,
    checks: &mut Vec<Check>,
    group: &'static str,
    name: &'static str,
    command: (&str, &[&str]),
    missing_status: Status,
    remedy: &str,
) {
    let (program, args) = command;
    match probe.command_output(program, args) {
        Some(output) => checks.push(Check::new(
            group,
            name,
            Status::Pass,
            first_line(&output),
            None,
        )),
        None => checks.push(Check::new(
            group,
            name,
            missing_status,
            format!("`{program} {}` failed or was not found", args.join(" ")),
            Some(remedy),
        )),
    }
}

fn executable_check(
    probe: &impl Probe,
    checks: &mut Vec<Check>,
    group: &'static str,
    name: &'static str,
    program: &str,
    remedy: &str,
) {
    match probe.executable(program) {
        Some(path) => checks.push(Check::new(
            group,
            name,
            Status::Pass,
            path.display().to_string(),
            None,
        )),
        None => checks.push(Check::new(
            group,
            name,
            Status::Fail,
            format!("`{program}` was not found on PATH"),
            Some(remedy),
        )),
    }
}

fn host_checks(config: &Config, probe: &impl Probe, checks: &mut Vec<Check>) {
    if cfg!(target_os = "linux") {
        checks.push(Check::new(
            "host",
            "operating-system",
            Status::Pass,
            "Linux",
            None,
        ));
    } else {
        checks.push(Check::new(
            "host",
            "operating-system",
            Status::Fail,
            env::consts::OS,
            Some("Run host-native NixOS VM benchmarks from a Linux host."),
        ));
    }

    for (name, program, args, remedy) in [
        ("bash", "bash", &["--version"][..], "Install Bash."),
        (
            "python",
            "python",
            &["--version"][..],
            "Install Python 3 with a `python` executable.",
        ),
        (
            "python-tomllib",
            "python",
            &[
                "-c",
                "import sys, tomllib; print('.'.join(map(str, sys.version_info[:3])))",
            ][..],
            "Install Python 3.11 or newer with the standard tomllib module.",
        ),
        ("jq", "jq", &["--version"][..], "Install jq."),
        ("curl", "curl", &["--version"][..], "Install curl."),
        (
            "nix-shell",
            "nix-shell",
            &["--version"][..],
            "Install Nix with nix-shell support.",
        ),
        ("ssh", "ssh", &["-V"][..], "Install an OpenSSH client."),
        ("ss", "ss", &["--version"][..], "Install iproute2."),
        ("tar", "tar", &["--version"][..], "Install tar."),
        ("flock", "flock", &["--version"][..], "Install util-linux."),
    ] {
        command_check(
            probe,
            checks,
            "host",
            name,
            (program, args),
            Status::Fail,
            remedy,
        );
    }
    executable_check(
        probe,
        checks,
        "host",
        "scp",
        "scp",
        "Install an OpenSSH client.",
    );
    executable_check(
        probe,
        checks,
        "host",
        "ssh-keygen",
        "ssh-keygen",
        "Install OpenSSH key utilities.",
    );

    let ssh_key = probe
        .env_var("REPLAYBOOK_HOST_SSH_KEY")
        .map(PathBuf::from)
        .or_else(|| dirs_next::home_dir().map(|home| home.join(".ssh/id_ed25519")));
    match ssh_key {
        Some(path)
            if probe.path_exists(&path)
                && probe.path_exists(Path::new(&format!("{}.pub", path.display()))) =>
        {
            checks.push(Check::new(
                "host",
                "ssh-keypair",
                Status::Pass,
                path.display().to_string(),
                None,
            ));
        }
        Some(path) => checks.push(Check::new(
            "host",
            "ssh-keypair",
            Status::Fail,
            format!("missing {} and/or its .pub file", path.display()),
            Some("Create a key pair with `ssh-keygen -t ed25519`, or set REPLAYBOOK_HOST_SSH_KEY."),
        )),
        None => checks.push(Check::new(
            "host",
            "ssh-keypair",
            Status::Fail,
            "home directory could not be resolved",
            Some("Set REPLAYBOOK_HOST_SSH_KEY to an Ed25519 private key."),
        )),
    }

    let kvm = Path::new("/dev/kvm");
    if probe.path_read_write(kvm) {
        checks.push(Check::new(
            "host",
            "kvm",
            Status::Pass,
            "/dev/kvm is readable and writable",
            None,
        ));
    } else {
        checks.push(Check::new(
            "host",
            "kvm",
            Status::Fail,
            "/dev/kvm is unavailable to this user",
            Some("Enable hardware virtualization and grant this user access to the kvm group."),
        ));
    }

    capacity_checks(probe, checks);
    port_check(config, probe, checks);
    harness_checks(probe, checks);
}

fn capacity_checks(probe: &impl Probe, checks: &mut Vec<Check>) {
    let disk_path = if probe.path_exists(Path::new("/nix")) {
        "/nix"
    } else {
        "."
    };
    match probe
        .command_output("df", &["-Pk", disk_path])
        .and_then(|output| parse_df_available_kib(&output))
    {
        Some(kib) => {
            let gib = kib / 1024 / 1024;
            let (status, remedy) = capacity_status(
                gib,
                HOST_DISK_PASS_GIB,
                HOST_DISK_WARN_GIB,
                "Free disk space or move REPLAYBOOK_HOST_TMPDIR to a larger filesystem.",
            );
            checks.push(Check::new(
                "capacity",
                "disk",
                status,
                format!("{gib} GiB available on {disk_path}"),
                remedy,
            ));
        }
        None => checks.push(Check::new(
            "capacity",
            "disk",
            Status::Fail,
            "could not determine available disk space",
            Some("Ensure POSIX `df` is available."),
        )),
    }

    match probe
        .read_to_string(Path::new("/proc/meminfo"))
        .and_then(|value| parse_mem_available_kib(&value))
    {
        Some(kib) => {
            let gib = kib / 1024 / 1024;
            let (status, remedy) = capacity_status(
                gib,
                HOST_MEMORY_PASS_GIB,
                HOST_MEMORY_WARN_GIB,
                "Reduce matrix concurrency or free memory before launching VMs.",
            );
            checks.push(Check::new(
                "capacity",
                "memory",
                status,
                format!("{gib} GiB available"),
                remedy,
            ));
        }
        None => checks.push(Check::new(
            "capacity",
            "memory",
            Status::Fail,
            "could not read MemAvailable from /proc/meminfo",
            Some("Run host-native benchmarks from a Linux host with procfs mounted."),
        )),
    }
}

fn capacity_status(
    gib: u64,
    pass: u64,
    warn: u64,
    remedy: &'static str,
) -> (Status, Option<&'static str>) {
    if gib >= pass {
        (Status::Pass, None)
    } else if gib >= warn {
        (Status::Warn, Some(remedy))
    } else {
        (Status::Fail, Some(remedy))
    }
}

fn port_check(config: &Config, probe: &impl Probe, checks: &mut Vec<Check>) {
    let count = match config.concurrency.checked_mul(2) {
        Some(count) if count > 0 => count,
        _ => {
            checks.push(Check::new(
                "host",
                "ports",
                Status::Fail,
                "concurrency must be greater than zero",
                Some("Pass `--concurrency` with the intended matrix concurrency."),
            ));
            return;
        }
    };
    let end = match config.base_port.checked_add(count - 1) {
        Some(end) => end,
        None => {
            checks.push(Check::new(
                "host",
                "ports",
                Status::Fail,
                "requested port range exceeds 65535",
                Some("Choose a lower --base-port or reduce --concurrency."),
            ));
            return;
        }
    };
    let ports: Vec<u16> = (config.base_port..=end).collect();
    match probe.ports_available(&ports) {
        Ok(()) => checks.push(Check::new(
            "host",
            "ports",
            Status::Pass,
            format!("127.0.0.1:{}-{end} available", config.base_port),
            None,
        )),
        Err(error) => checks.push(Check::new(
            "host",
            "ports",
            Status::Fail,
            error,
            Some("Stop the listener or select a different --base-port."),
        )),
    }
}

fn harness_checks(probe: &impl Probe, checks: &mut Vec<Check>) {
    let mut found = Vec::new();
    for (name, program) in [
        ("Claux", "claux"),
        ("OpenCode", "opencode"),
        ("Codex", "codex"),
    ] {
        if let Some(output) = probe.command_output(program, &["--version"]) {
            found.push(format!("{name}: {}", first_line(&output)));
        }
    }
    if let Some(path) = probe.env_var("REPLAYBOOK_HOST_CLAUX_BINARY")
        && probe.path_exists(Path::new(&path))
    {
        found.push(format!("Claux payload: {path}"));
    }
    if found.is_empty() {
        checks.push(Check::new(
            "agent",
            "harness",
            Status::Info,
            "no local agent harness detected; oracle runs remain available",
            Some("Install Claux, OpenCode, or Codex before running model evaluations."),
        ));
    } else {
        checks.push(Check::new(
            "agent",
            "harness",
            Status::Pass,
            found.join("; "),
            None,
        ));
    }

    let key_names = ["REPLAYBOOK_OPENAI_API_KEY", "OPENROUTER_API_KEY"];
    let configured: Vec<_> = key_names
        .iter()
        .copied()
        .filter(|name| probe.env_var(name).is_some())
        .collect();
    if configured.is_empty() {
        checks.push(Check::new(
            "agent",
            "provider-credentials",
            Status::Info,
            "no OpenRouter credential environment variable detected",
            Some("Set REPLAYBOOK_OPENAI_API_KEY or OPENROUTER_API_KEY for the default Claux adapter."),
        ));
    } else {
        checks.push(Check::new(
            "agent",
            "provider-credentials",
            Status::Pass,
            format!("configured via {}", configured.join(", ")),
            None,
        ));
    }
}

fn first_line(value: &str) -> String {
    value.lines().next().unwrap_or(value).trim().to_owned()
}

fn count(checks: &[Check], status: Status) -> usize {
    checks.iter().filter(|check| check.status == status).count()
}

fn parse_df_available_kib(output: &str) -> Option<u64> {
    output
        .lines()
        .rfind(|line| !line.trim().is_empty())?
        .split_whitespace()
        .nth(3)?
        .parse()
        .ok()
}

fn parse_mem_available_kib(output: &str) -> Option<u64> {
    output.lines().find_map(|line| {
        let rest = line.strip_prefix("MemAvailable:")?;
        rest.split_whitespace().next()?.parse().ok()
    })
}

pub fn print_human(report: &Report) {
    println!("Replaybook doctor {}", report.replaybook_version);
    for check in &report.checks {
        println!(
            "[{:<4}] {:<8} {:<22} {}",
            check.status.marker(),
            check.group,
            check.name,
            check.detail
        );
        if let Some(remedy) = &check.remedy {
            println!("       fix: {remedy}");
        }
    }
    println!(
        "\n{} passed, {} warnings, {} failed, {} informational",
        report.summary.passed,
        report.summary.warnings,
        report.summary.failed,
        report.summary.informational
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::{HashMap, HashSet};

    #[derive(Default)]
    struct FakeProbe {
        commands: HashMap<String, String>,
        paths: HashSet<PathBuf>,
        read_write_paths: HashSet<PathBuf>,
        files: HashMap<PathBuf, String>,
        environment: HashMap<String, String>,
        port_error: Option<String>,
    }

    impl Probe for FakeProbe {
        fn command_output(&self, program: &str, args: &[&str]) -> Option<String> {
            self.commands
                .get(&format!("{program} {}", args.join(" ")))
                .cloned()
        }

        fn executable(&self, program: &str) -> Option<PathBuf> {
            let path = PathBuf::from(format!("/bin/{program}"));
            self.paths.contains(&path).then_some(path)
        }

        fn path_exists(&self, path: &Path) -> bool {
            self.paths.contains(path)
        }

        fn path_read_write(&self, path: &Path) -> bool {
            self.read_write_paths.contains(path)
        }

        fn read_to_string(&self, path: &Path) -> Option<String> {
            self.files.get(path).cloned()
        }

        fn env_var(&self, name: &str) -> Option<String> {
            self.environment.get(name).cloned()
        }

        fn ports_available(&self, _ports: &[u16]) -> Result<(), String> {
            self.port_error.clone().map_or(Ok(()), Err)
        }
    }

    fn config(host: bool) -> Config {
        Config {
            host,
            base_port: 26000,
            concurrency: 2,
            scenarios_dir: PathBuf::from("/scenarios"),
        }
    }

    fn healthy_core() -> FakeProbe {
        let mut probe = FakeProbe::default();
        for (command, output) in [
            ("docker --version", "Docker version 29"),
            ("docker compose version", "Docker Compose version v2"),
            ("docker info --format {{.ServerVersion}}", "29.0.0"),
            ("git --version", "git version 2.50"),
        ] {
            probe.commands.insert(command.into(), output.into());
        }
        probe.paths.insert(PathBuf::from("/scenarios"));
        probe
    }

    #[test]
    fn core_doctor_passes_with_docker_and_scenarios() {
        let report = inspect_with(&config(false), &healthy_core());
        assert!(report.healthy());
        assert_eq!(report.summary.passed, 5);
        assert_eq!(report.summary.failed, 0);
        assert!(!report.host_benchmark);
    }

    #[test]
    fn missing_docker_fails_but_missing_pack_only_warns() {
        let report = inspect_with(&config(false), &FakeProbe::default());
        assert!(!report.healthy());
        assert_eq!(report.summary.failed, 3);
        assert_eq!(report.summary.warnings, 2);
    }

    #[test]
    fn parses_capacity_sources() {
        assert_eq!(
            parse_df_available_kib(
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 100 20 80 20% /nix\n"
            ),
            Some(80)
        );
        assert_eq!(
            parse_mem_available_kib("MemTotal: 100 kB\nMemAvailable: 4242 kB\n"),
            Some(4242)
        );
    }

    #[test]
    fn overflowing_port_range_is_a_failure() {
        let mut probe = healthy_core();
        probe.paths.insert(PathBuf::from("/nix"));
        probe.commands.insert(
            "df -Pk /nix".into(),
            "fs 1K-blocks Used Available Use% Mounted\nx 100 0 50000000 0% /nix".into(),
        );
        probe.files.insert(
            PathBuf::from("/proc/meminfo"),
            "MemAvailable: 9000000 kB".into(),
        );
        let mut value = config(true);
        value.base_port = 65535;
        value.concurrency = 2;
        let report = inspect_with(&value, &probe);
        assert!(
            report
                .checks
                .iter()
                .any(|check| check.name == "ports" && check.status == Status::Fail)
        );
    }

    #[test]
    fn provider_check_reports_variable_name_without_secret_value() {
        let mut probe = FakeProbe::default();
        probe
            .environment
            .insert("OPENROUTER_API_KEY".into(), "never-print-me".into());
        let mut checks = Vec::new();
        harness_checks(&probe, &mut checks);
        let json = serde_json::to_string(&checks).unwrap();
        assert!(json.contains("OPENROUTER_API_KEY"));
        assert!(!json.contains("never-print-me"));
    }
}
