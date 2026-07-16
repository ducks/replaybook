use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SuccessCondition {
    #[serde(rename = "http_200")]
    Http200,
    #[serde(rename = "exit_zero")]
    ExitZero,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BreakStep {
    /// Copy a file from the scenario directory into a container.
    Cp {
        service: String,
        src: String,
        dest: String,
    },
    /// Run a command inside a service's container.
    Exec { service: String, cmd: Vec<String> },
    /// Restart a service's container.
    Restart { service: String },
}

/// One of several possible root causes behind a scenario's symptom. When a
/// scenario defines faults, `run` draws one at random - the page stays the
/// same, so repeat runs stay diagnostic instead of becoming memorization.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fault {
    pub name: String,
    /// Declarative injection steps, like the scenario-level `break`.
    #[serde(rename = "break", default)]
    pub break_steps: Option<Vec<BreakStep>>,
    /// A script filename in the scenario dir, for faults that need real
    /// script logic. Used when `break` is absent.
    #[serde(default)]
    pub script: Option<String>,
    /// Per-fault hints. Falls back to the scenario-level hints if empty.
    #[serde(default)]
    pub hints: Vec<String>,
    /// Per-fault solve script filename. Falls back to solve.sh.
    #[serde(default)]
    pub solve: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScenarioMeta {
    pub id: String,
    pub title: String,
    pub page: String,
    pub difficulty: u8,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub hints: Vec<String>,
    pub success_condition: SuccessCondition,
    #[serde(default)]
    pub success_target: String,
    /// Declarative fault injection steps, run instead of break.sh if present.
    #[serde(rename = "break", default)]
    pub break_steps: Option<Vec<BreakStep>>,
    /// Alternative root causes; one is selected per run.
    #[serde(default)]
    pub faults: Vec<Fault>,
    /// Optional link back to the sanitized incident that inspired the drill.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<IncidentSource>,
    /// Skills a player should practice by completing the scenario.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub learning_objectives: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IncidentSource {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub incident_date: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reference: Option<String>,
    #[serde(default)]
    pub sanitized: bool,
}

/// The fault actually being played this run, with every fallback resolved.
#[derive(Debug, Clone)]
pub struct ActiveFault {
    /// None for single-fault scenarios that don't use the faults list.
    pub name: Option<String>,
    pub break_steps: Option<Vec<BreakStep>>,
    pub break_script: PathBuf,
    pub hints: Vec<String>,
    pub solve_script: PathBuf,
}

#[derive(Debug, Clone)]
pub struct Scenario {
    pub meta: ScenarioMeta,
    pub dir: PathBuf,
}

impl Scenario {
    pub fn load(dir: &Path) -> Result<Self> {
        let meta_path = dir.join("meta.json");
        let raw = std::fs::read_to_string(&meta_path)
            .with_context(|| format!("reading {}", meta_path.display()))?;
        let meta: ScenarioMeta = serde_json::from_str(&raw)
            .with_context(|| format!("parsing {}", meta_path.display()))?;
        Ok(Self {
            meta,
            dir: dir.to_path_buf(),
        })
    }

    pub fn compose_file(&self) -> PathBuf {
        self.dir.join("docker-compose.yml")
    }

    pub fn break_script(&self) -> PathBuf {
        self.dir.join("break.sh")
    }

    pub fn check_script(&self) -> PathBuf {
        self.dir.join("check.sh")
    }

    pub fn solve_script(&self) -> PathBuf {
        self.dir.join("solve.sh")
    }

    /// Resolve which fault this run plays. `requested` forces a named fault;
    /// otherwise `seed` picks one (callers pass something arbitrary like
    /// clock nanos). Scenarios without a faults list resolve to their
    /// scenario-level break/hints/solve.
    pub fn select_fault(&self, requested: Option<&str>, seed: usize) -> Result<ActiveFault> {
        if self.meta.faults.is_empty() {
            if let Some(name) = requested {
                anyhow::bail!(
                    "scenario '{}' does not define named faults (asked for \"{name}\")",
                    self.meta.id
                );
            }
            return Ok(ActiveFault {
                name: None,
                break_steps: self.meta.break_steps.clone(),
                break_script: self.break_script(),
                hints: self.meta.hints.clone(),
                solve_script: self.solve_script(),
            });
        }

        let fault = match requested {
            Some(name) => self
                .meta
                .faults
                .iter()
                .find(|f| f.name == name)
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "scenario '{}' has no fault \"{name}\" (available: {})",
                        self.meta.id,
                        self.meta
                            .faults
                            .iter()
                            .map(|f| f.name.as_str())
                            .collect::<Vec<_>>()
                            .join(", ")
                    )
                })?,
            None => &self.meta.faults[seed % self.meta.faults.len()],
        };

        Ok(ActiveFault {
            name: Some(fault.name.clone()),
            break_steps: fault.break_steps.clone(),
            break_script: fault
                .script
                .as_ref()
                .map(|s| self.dir.join(s))
                .unwrap_or_else(|| self.break_script()),
            hints: if fault.hints.is_empty() {
                self.meta.hints.clone()
            } else {
                fault.hints.clone()
            },
            solve_script: fault
                .solve
                .as_ref()
                .map(|s| self.dir.join(s))
                .unwrap_or_else(|| self.solve_script()),
        })
    }
}

fn is_dotdir(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.starts_with('.'))
}

pub fn discover(scenarios_dir: &Path) -> Result<Vec<Scenario>> {
    discover_with_mode(scenarios_dir, false)
}

/// Discover a scenario pack for CI. Unlike regular discovery, malformed
/// scenario metadata is an error instead of something to skip.
pub fn discover_strict(scenarios_dir: &Path) -> Result<Vec<Scenario>> {
    discover_with_mode(scenarios_dir, true)
}

fn load_discovered(dir: &Path, strict: bool, scenarios: &mut Vec<Scenario>) -> Result<()> {
    match Scenario::load(dir) {
        Ok(scenario) => scenarios.push(scenario),
        Err(error) if strict => return Err(error),
        Err(error) => eprintln!("skipping {}: {error}", dir.display()),
    }
    Ok(())
}

fn discover_with_mode(scenarios_dir: &Path, strict: bool) -> Result<Vec<Scenario>> {
    let mut scenarios = Vec::new();
    let mut entries: Vec<_> = std::fs::read_dir(scenarios_dir)
        .with_context(|| format!("reading {}", scenarios_dir.display()))?
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .filter(|e| !is_dotdir(&e.path()))
        .collect();
    entries.sort_by_key(|e| e.path());

    for entry in entries {
        let dir = entry.path();
        if dir.join("meta.json").exists() {
            // A scenario placed directly under scenarios_dir.
            load_discovered(&dir, strict, &mut scenarios)?;
            continue;
        }

        // Otherwise treat this as a scenario pack: scan its immediate
        // children (add clones packs into scenarios_dir/<pack-name>/).
        let mut pack_entries: Vec<_> = match std::fs::read_dir(&dir) {
            Ok(entries) => entries
                .filter_map(|e| e.ok())
                .filter(|e| e.path().is_dir())
                .filter(|e| !is_dotdir(&e.path()))
                .collect(),
            Err(_) => continue,
        };
        pack_entries.sort_by_key(|e| e.path());

        for pack_entry in pack_entries {
            let scenario_dir = pack_entry.path();
            // Repositories can also contain docs and other directories. A
            // meta.json marks an actual scenario; malformed metadata should
            // fail strict discovery rather than being silently omitted.
            if scenario_dir.join("meta.json").exists() {
                load_discovered(&scenario_dir, strict, &mut scenarios)?;
            }
        }
    }
    Ok(scenarios)
}

#[cfg(test)]
mod tests {
    use super::*;

    const MINIMAL_META: &str = r#"{
        "id": "my-scenario",
        "title": "Something Is Broken",
        "page": "alert text",
        "difficulty": 2,
        "hints": ["hint one"],
        "success_condition": "http_200",
        "success_target": "http://localhost:8080/health"
    }"#;

    fn write_scenario(dir: &Path, meta: &str) {
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(dir.join("meta.json"), meta).unwrap();
    }

    #[test]
    fn tags_and_success_target_are_optional() {
        // This is the exact shape the README documents - it must parse.
        let meta: ScenarioMeta = serde_json::from_str(
            r#"{
                "id": "x",
                "title": "t",
                "page": "p",
                "difficulty": 1,
                "hints": [],
                "success_condition": "exit_zero"
            }"#,
        )
        .unwrap();
        assert!(meta.tags.is_empty());
        assert_eq!(meta.success_target, "");
        assert!(meta.break_steps.is_none());
        assert!(meta.source.is_none());
        assert!(meta.learning_objectives.is_empty());
    }

    #[test]
    fn parses_incident_provenance_and_learning_objectives() {
        let meta: ScenarioMeta = serde_json::from_str(
            r#"{
                "id": "x", "title": "t", "page": "p", "difficulty": 1,
                "hints": [], "success_condition": "exit_zero",
                "source": {
                    "incident_date": "2026-06-14",
                    "reference": "INC-1842",
                    "sanitized": true
                },
                "learning_objectives": ["Recognize pool exhaustion"]
            }"#,
        )
        .unwrap();
        let source = meta.source.unwrap();
        assert_eq!(source.incident_date.as_deref(), Some("2026-06-14"));
        assert_eq!(source.reference.as_deref(), Some("INC-1842"));
        assert!(source.sanitized);
        assert_eq!(meta.learning_objectives, ["Recognize pool exhaustion"]);
    }

    #[test]
    fn parses_declarative_break_steps() {
        let meta: ScenarioMeta = serde_json::from_str(
            r#"{
                "id": "x",
                "title": "t",
                "page": "p",
                "difficulty": 1,
                "hints": [],
                "success_condition": "http_200",
                "success_target": "http://localhost:8080/",
                "break": [
                    { "cp": { "service": "app", "src": "a.conf", "dest": "/etc/a.conf" } },
                    { "exec": { "service": "app", "cmd": ["nginx", "-s", "reload"] } },
                    { "restart": { "service": "app" } }
                ]
            }"#,
        )
        .unwrap();
        let steps = meta.break_steps.unwrap();
        assert_eq!(steps.len(), 3);
        assert!(matches!(&steps[0], BreakStep::Cp { service, .. } if service == "app"));
        assert!(
            matches!(&steps[1], BreakStep::Exec { cmd, .. } if cmd == &["nginx", "-s", "reload"])
        );
        assert!(matches!(&steps[2], BreakStep::Restart { .. }));
    }

    #[test]
    fn rejects_unknown_success_condition() {
        let result = serde_json::from_str::<ScenarioMeta>(
            r#"{
                "id": "x",
                "title": "t",
                "page": "p",
                "difficulty": 1,
                "hints": [],
                "success_condition": "always_win"
            }"#,
        );
        assert!(result.is_err());
    }

    #[test]
    fn discover_finds_direct_and_pack_nested_scenarios() {
        let root = tempfile::tempdir().unwrap();

        // A scenario directly under the scenarios dir.
        write_scenario(&root.path().join("000-direct"), MINIMAL_META);

        // Two scenarios nested inside a cloned pack.
        let pack = root.path().join("some-pack");
        write_scenario(
            &pack.join("001-b"),
            &MINIMAL_META.replace("my-scenario", "001-b"),
        );
        write_scenario(
            &pack.join("002-a"),
            &MINIMAL_META.replace("my-scenario", "002-a"),
        );

        // Noise that must be ignored: dotdirs and files.
        write_scenario(&root.path().join(".git"), MINIMAL_META);
        std::fs::write(root.path().join("README.md"), "hi").unwrap();

        let scenarios = discover(root.path()).unwrap();
        let ids: Vec<&str> = scenarios.iter().map(|s| s.meta.id.as_str()).collect();
        assert_eq!(ids, vec!["my-scenario", "001-b", "002-a"]);
    }

    #[test]
    fn discover_skips_invalid_meta_without_failing() {
        let root = tempfile::tempdir().unwrap();
        write_scenario(&root.path().join("001-good"), MINIMAL_META);
        write_scenario(&root.path().join("002-bad"), "{ not json");

        let scenarios = discover(root.path()).unwrap();
        assert_eq!(scenarios.len(), 1);
        assert_eq!(scenarios[0].meta.id, "my-scenario");
    }

    #[test]
    fn strict_discovery_rejects_invalid_meta() {
        let root = tempfile::tempdir().unwrap();
        write_scenario(&root.path().join("001-good"), MINIMAL_META);
        write_scenario(&root.path().join("002-bad"), "{ not json");

        let error = discover_strict(root.path()).unwrap_err().to_string();
        assert!(error.contains("parsing"), "{error}");
        assert!(error.contains("002-bad/meta.json"), "{error}");
    }

    #[test]
    fn scenario_paths_derive_from_dir() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("001-x");
        write_scenario(&dir, MINIMAL_META);
        let s = Scenario::load(&dir).unwrap();
        assert_eq!(s.compose_file(), dir.join("docker-compose.yml"));
        assert_eq!(s.break_script(), dir.join("break.sh"));
        assert_eq!(s.check_script(), dir.join("check.sh"));
        assert_eq!(s.solve_script(), dir.join("solve.sh"));
    }

    const MULTI_FAULT_META: &str = r#"{
        "id": "jobs-not-processing",
        "title": "Jobs Not Processing",
        "page": "background work stopped",
        "difficulty": 2,
        "hints": ["shared fallback hint"],
        "success_condition": "exit_zero",
        "faults": [
            { "name": "redis-auth",
              "break": [ { "restart": { "service": "sidekiq" } } ],
              "hints": ["auth hint"],
              "solve": "solve-auth.sh" },
            { "name": "redis-stopped",
              "script": "break-stopped.sh" }
        ]
    }"#;

    fn multi_fault_scenario(dir: &Path) -> Scenario {
        Scenario {
            meta: serde_json::from_str(MULTI_FAULT_META).unwrap(),
            dir: dir.to_path_buf(),
        }
    }

    #[test]
    fn select_fault_without_faults_list_resolves_scenario_level() {
        let s = Scenario {
            meta: serde_json::from_str(MINIMAL_META).unwrap(),
            dir: PathBuf::from("/s"),
        };
        let f = s.select_fault(None, 7).unwrap();
        assert_eq!(f.name, None);
        assert_eq!(f.break_script, PathBuf::from("/s/break.sh"));
        assert_eq!(f.solve_script, PathBuf::from("/s/solve.sh"));
        assert_eq!(f.hints, vec!["hint one"]);

        // Asking for a named fault on a single-fault scenario is an error.
        assert!(s.select_fault(Some("anything"), 0).is_err());
    }

    #[test]
    fn select_fault_by_name_resolves_overrides_and_fallbacks() {
        let s = multi_fault_scenario(Path::new("/s"));

        let auth = s.select_fault(Some("redis-auth"), 0).unwrap();
        assert_eq!(auth.name.as_deref(), Some("redis-auth"));
        assert!(auth.break_steps.is_some());
        assert_eq!(auth.hints, vec!["auth hint"]);
        assert_eq!(auth.solve_script, PathBuf::from("/s/solve-auth.sh"));

        let stopped = s.select_fault(Some("redis-stopped"), 0).unwrap();
        assert!(stopped.break_steps.is_none());
        assert_eq!(stopped.break_script, PathBuf::from("/s/break-stopped.sh"));
        // Falls back to scenario-level hints and solve.sh.
        assert_eq!(stopped.hints, vec!["shared fallback hint"]);
        assert_eq!(stopped.solve_script, PathBuf::from("/s/solve.sh"));

        let err = s.select_fault(Some("nope"), 0).unwrap_err().to_string();
        assert!(err.contains("redis-auth, redis-stopped"), "{err}");
    }

    #[test]
    fn select_fault_seed_picks_deterministically_and_in_bounds() {
        let s = multi_fault_scenario(Path::new("/s"));
        let names: Vec<_> = (0..4)
            .map(|seed| s.select_fault(None, seed).unwrap().name.unwrap())
            .collect();
        assert_eq!(
            names,
            vec!["redis-auth", "redis-stopped", "redis-auth", "redis-stopped"]
        );
    }

    #[test]
    fn discovers_direct_and_packed_scenarios_in_stable_order() {
        let root = tempfile::tempdir().unwrap();
        write_scenario(
            &root.path().join("020-direct"),
            &MINIMAL_META.replace("my-scenario", "020-direct"),
        );

        let pack = root.path().join("scenario-pack");
        write_scenario(
            &pack.join("010-packed"),
            &MINIMAL_META.replace("my-scenario", "010-packed"),
        );
        write_scenario(
            &pack.join("030-packed"),
            &MINIMAL_META.replace("my-scenario", "030-packed"),
        );

        let scenarios = discover(root.path()).expect("scenario discovery should succeed");
        let ids: Vec<_> = scenarios.iter().map(|s| s.meta.id.as_str()).collect();

        assert_eq!(ids, ["020-direct", "010-packed", "030-packed"]);
    }

    #[test]
    fn ignores_dot_directories_and_skips_malformed_pack_scenarios() {
        let root = tempfile::tempdir().unwrap();
        write_scenario(
            &root.path().join("valid-direct"),
            &MINIMAL_META.replace("my-scenario", "valid-direct"),
        );
        write_scenario(
            &root.path().join(".hidden-direct"),
            &MINIMAL_META.replace("my-scenario", "hidden-direct"),
        );

        let pack = root.path().join("pack");
        write_scenario(
            &pack.join("valid-packed"),
            &MINIMAL_META.replace("my-scenario", "valid-packed"),
        );
        write_scenario(
            &pack.join(".hidden-packed"),
            &MINIMAL_META.replace("my-scenario", "hidden-packed"),
        );

        let malformed = pack.join("malformed");
        std::fs::create_dir_all(&malformed).expect("failed to create malformed dir");
        std::fs::write(malformed.join("meta.json"), "{not json").expect("failed to write bad json");

        let scenarios = discover(root.path()).expect("scenario discovery should succeed");
        let ids: Vec<_> = scenarios.iter().map(|s| s.meta.id.as_str()).collect();

        assert_eq!(ids, ["valid-packed", "valid-direct"]);
    }
}
