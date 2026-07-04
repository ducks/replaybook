use anyhow::Result;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Serialize, Deserialize)]
pub struct SessionRecord {
    pub scenario_id: String,
    pub started_at: String,
    pub outcome: Outcome,
    pub elapsed_secs: Option<u64>,
    pub hints_used: u8,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transcript: Option<String>,
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
    transcript: Option<&Path>,
) -> Result<()> {
    record_to(
        &sessions_dir(),
        scenario_id,
        outcome,
        elapsed,
        hints_used,
        transcript,
    )
}

fn record_to(
    dir: &Path,
    scenario_id: &str,
    outcome: Outcome,
    elapsed: Option<Duration>,
    hints_used: u8,
    transcript: Option<&Path>,
) -> Result<()> {
    let record = SessionRecord {
        scenario_id: scenario_id.to_string(),
        started_at: Utc::now().to_rfc3339(),
        outcome,
        elapsed_secs: elapsed.map(|d| d.as_secs()),
        hints_used,
        transcript: transcript.map(|p| p.display().to_string()),
    };

    fs::create_dir_all(dir)?;
    let path = dir.join("sessions.jsonl");

    let mut f = OpenOptions::new().create(true).append(true).open(&path)?;
    writeln!(f, "{}", serde_json::to_string(&record)?)?;
    Ok(())
}

pub fn sessions_dir() -> PathBuf {
    dirs_next::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join("replaybook")
        .join("sessions")
}

pub fn transcripts_dir() -> PathBuf {
    sessions_dir().join("transcripts")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read_lines(dir: &Path) -> Vec<String> {
        fs::read_to_string(dir.join("sessions.jsonl"))
            .unwrap()
            .lines()
            .map(String::from)
            .collect()
    }

    #[test]
    fn record_appends_jsonl() {
        let dir = tempfile::tempdir().unwrap();
        record_to(
            dir.path(),
            "001-nginx-502",
            Outcome::Success,
            Some(Duration::from_secs(90)),
            2,
            None,
        )
        .unwrap();
        record_to(dir.path(), "001-nginx-502", Outcome::Timeout, None, 1, None).unwrap();

        let lines = read_lines(dir.path());
        assert_eq!(lines.len(), 2);

        let first: SessionRecord = serde_json::from_str(&lines[0]).unwrap();
        assert_eq!(first.scenario_id, "001-nginx-502");
        assert_eq!(first.elapsed_secs, Some(90));
        assert_eq!(first.hints_used, 2);
        assert!(matches!(first.outcome, Outcome::Success));

        let second: SessionRecord = serde_json::from_str(&lines[1]).unwrap();
        assert_eq!(second.elapsed_secs, None);
        assert!(matches!(second.outcome, Outcome::Timeout));
    }

    #[test]
    fn transcript_field_only_serialized_when_present() {
        let dir = tempfile::tempdir().unwrap();
        record_to(dir.path(), "s", Outcome::Abandoned, None, 0, None).unwrap();
        record_to(
            dir.path(),
            "s",
            Outcome::Success,
            None,
            0,
            Some(Path::new("/tmp/t.log")),
        )
        .unwrap();

        let lines = read_lines(dir.path());
        assert!(!lines[0].contains("transcript"));
        assert!(lines[1].contains("\"transcript\":\"/tmp/t.log\""));
    }

    #[test]
    fn deserializes_records_written_before_transcripts_existed() {
        let old = r#"{"scenario_id":"x","started_at":"2026-07-01T00:00:00Z","outcome":"success","elapsed_secs":10,"hints_used":0}"#;
        let rec: SessionRecord = serde_json::from_str(old).unwrap();
        assert_eq!(rec.transcript, None);
    }
}
