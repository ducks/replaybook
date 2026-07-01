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
    pub tags: Vec<String>,
    pub hints: Vec<String>,
    pub success_condition: SuccessCondition,
    pub success_target: String,
    /// Which compose service to exec into. Defaults to the first container.
    #[serde(default)]
    pub shell_service: Option<String>,
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
