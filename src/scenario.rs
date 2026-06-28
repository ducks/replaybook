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
pub struct ScenarioMeta {
    pub id: String,
    pub title: String,
    pub page: String,
    pub difficulty: u8,
    pub tags: Vec<String>,
    pub hints: Vec<String>,
    pub success_condition: SuccessCondition,
    pub success_target: String,
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
        let meta: ScenarioMeta =
            serde_json::from_str(&raw).with_context(|| format!("parsing {}", meta_path.display()))?;
        Ok(Self { meta, dir: dir.to_path_buf() })
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

pub fn discover(scenarios_dir: &Path) -> Result<Vec<Scenario>> {
    let mut scenarios = Vec::new();
    let mut entries: Vec<_> = std::fs::read_dir(scenarios_dir)
        .with_context(|| format!("reading {}", scenarios_dir.display()))?
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .collect();
    entries.sort_by_key(|e| e.path());

    for entry in entries {
        match Scenario::load(&entry.path()) {
            Ok(s) => scenarios.push(s),
            Err(e) => eprintln!("skipping {}: {e}", entry.path().display()),
        }
    }
    Ok(scenarios)
}
