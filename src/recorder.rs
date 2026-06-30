use anyhow::Result;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Debug, Serialize, Deserialize)]
pub struct SessionRecord {
    pub scenario_id: String,
    pub started_at: String,
    pub outcome: Outcome,
    pub elapsed_secs: Option<u64>,
    pub hints_used: u8,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Success,
    Timeout,
    Abandoned,
}

pub fn record(
    scenario_id: &str,
    outcome: Outcome,
    elapsed: Option<Duration>,
    hints_used: u8,
) -> Result<()> {
    let record = SessionRecord {
        scenario_id: scenario_id.to_string(),
        started_at: Utc::now().to_rfc3339(),
        outcome,
        elapsed_secs: elapsed.map(|d| d.as_secs()),
        hints_used,
    };

    let dir = sessions_dir();
    fs::create_dir_all(&dir)?;
    let path = dir.join("sessions.jsonl");

    let mut f = OpenOptions::new().create(true).append(true).open(&path)?;
    writeln!(f, "{}", serde_json::to_string(&record)?)?;
    Ok(())
}

fn sessions_dir() -> PathBuf {
    dirs_next().join("replaybook").join("sessions")
}

fn dirs_next() -> PathBuf {
    std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/tmp"))
        .join(".local")
        .join("share")
}
