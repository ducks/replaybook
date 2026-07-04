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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScenarioMeta {
    pub id: String,
    pub title: String,
    pub page: String,
    pub difficulty: u8,
    #[serde(default)]
    pub tags: Vec<String>,
    pub hints: Vec<String>,
    pub success_condition: SuccessCondition,
    #[serde(default)]
    pub success_target: String,
    /// Declarative fault injection steps, run instead of break.sh if present.
    #[serde(rename = "break", default)]
    pub break_steps: Option<Vec<BreakStep>>,
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
}

fn is_dotdir(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.starts_with('.'))
}

pub fn discover(scenarios_dir: &Path) -> Result<Vec<Scenario>> {
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
            match Scenario::load(&dir) {
                Ok(s) => scenarios.push(s),
                Err(e) => eprintln!("skipping {}: {e}", dir.display()),
            }
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
            match Scenario::load(&scenario_dir) {
                Ok(s) => scenarios.push(s),
                Err(e) => eprintln!("skipping {}: {e}", scenario_dir.display()),
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
}
