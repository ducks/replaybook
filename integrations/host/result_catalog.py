#!/usr/bin/env python3
"""Build and query a local catalog of Replaybook host-matrix results."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from integrations.host.benchmark_stats import wilson_interval
from integrations.host.publish_benchmarks import PublishError, compact_recording


REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = REPO_DIR / "jobs" / "result-catalog.sqlite3"
SUPPORTED_SUITE = "replaybook-host-matrix-v1"
SCHEMA_VERSION = 1


class CatalogError(ValueError):
    """An artifact cannot be represented safely in the local catalog."""


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matrices (
    matrix_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    suite TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    replaybook_commit TEXT,
    harness_versions_json TEXT NOT NULL,
    benchmark_manifest_json TEXT,
    host_harness_sha256 TEXT,
    agent_name TEXT,
    agent_adapter TEXT,
    claux_release TEXT,
    expected_trials INTEGER,
    received_results INTEGER,
    imported_at TEXT NOT NULL,
    benchmark_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trials (
    trial_id TEXT PRIMARY KEY,
    matrix_id TEXT NOT NULL REFERENCES matrices(matrix_id) ON DELETE CASCADE,
    compatibility_id TEXT NOT NULL,
    compatibility_json TEXT NOT NULL,
    run_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    scenario_version INTEGER NOT NULL,
    pack_id TEXT,
    pack_version TEXT,
    harness_version INTEGER,
    agent TEXT NOT NULL,
    agent_adapter TEXT,
    model TEXT NOT NULL,
    reasoning_effort TEXT,
    attempt INTEGER NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    duration_seconds REAL,
    timeout_seconds INTEGER,
    trial_status TEXT NOT NULL,
    reward INTEGER NOT NULL,
    failure_category TEXT,
    failure TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    cost_usd REAL,
    immediate_http INTEGER,
    service_restart INTEGER,
    host_reboot INTEGER,
    durable_after_timeout INTEGER NOT NULL DEFAULT 0,
    model_rounds REAL,
    model_duration_seconds REAL,
    tool_calls REAL,
    tool_duration_seconds REAL,
    first_non_read_only_seconds REAL,
    post_first_non_read_only_seconds REAL,
    result_path TEXT,
    transcript_path TEXT,
    log_path TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(matrix_id, run_id)
);

CREATE TABLE IF NOT EXISTS matrix_errors (
    matrix_id TEXT NOT NULL REFERENCES matrices(matrix_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    exit_code INTEGER,
    error TEXT,
    log_path TEXT,
    PRIMARY KEY (matrix_id, run_id)
);

CREATE INDEX IF NOT EXISTS trials_scenario_idx
    ON trials(scenario, scenario_version, compatibility_id);
CREATE INDEX IF NOT EXISTS trials_model_idx
    ON trials(model, reasoning_effort);
CREATE INDEX IF NOT EXISTS trials_matrix_idx ON trials(matrix_id);
"""


@dataclass(frozen=True)
class ImportStats:
    matrices: int = 0
    trials: int = 0
    errors: int = 0
    skipped: int = 0

    def add(self, other: "ImportStats") -> "ImportStats":
        return ImportStats(
            matrices=self.matrices + other.matrices,
            trials=self.trials + other.trials,
            errors=self.errors + other.errors,
            skipped=self.skipped + other.skipped,
        )


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(database: Path) -> sqlite3.Connection:
    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    current = connection.execute(
        "SELECT value FROM catalog_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if current is not None and int(current["value"]) != SCHEMA_VERSION:
        connection.close()
        raise CatalogError(
            f"catalog schema is v{current['value']}; expected v{SCHEMA_VERSION}"
        )
    connection.execute(
        "INSERT OR REPLACE INTO catalog_metadata(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    connection.commit()
    return connection


def summary_paths(inputs: Iterable[Path]) -> list[Path]:
    found: dict[Path, None] = {}
    for supplied in inputs:
        path = supplied.expanduser().resolve()
        if path.is_file():
            if path.name != "summary.json":
                raise CatalogError(f"expected a summary.json file: {path}")
            found[path] = None
        elif path.is_dir():
            for summary in path.rglob("summary.json"):
                if summary.is_file():
                    found[summary.resolve()] = None
        else:
            raise CatalogError(f"result path does not exist: {path}")
    return sorted(found)


def read_summary(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"could not read {path}: {error}") from error
    if not isinstance(value, dict):
        raise CatalogError(f"{path}: expected a JSON object")
    return value


def matrix_identity(summary: dict[str, Any]) -> dict[str, Any]:
    benchmark = summary.get("benchmark") or {}
    return {
        "schema_version": 1,
        "suite": summary.get("suite"),
        "started_at": benchmark.get("started_at") or summary.get("started_at"),
        "replaybook_commit": benchmark.get("replaybook_commit"),
        "benchmark_manifest": benchmark.get("benchmark_manifest"),
        "execution_snapshot": benchmark.get("execution_snapshot"),
        "scenarios": benchmark.get("scenarios"),
        "models": benchmark.get("models"),
        "reasoning_efforts": benchmark.get("reasoning_efforts"),
        "attempts": benchmark.get("attempts"),
        "agent_timeout_seconds": benchmark.get("agent_timeout_seconds"),
        "agent": benchmark.get("agent"),
        "claux_release": benchmark.get("claux_release"),
    }


def scenario_pack(
    run: dict[str, Any], benchmark: dict[str, Any]
) -> tuple[str | None, str | None]:
    pack = run.get("scenario_pack")
    if not isinstance(pack, dict):
        for scenario in benchmark.get("scenarios") or []:
            if isinstance(scenario, dict) and scenario.get("id") == run.get("scenario"):
                pack = scenario.get("pack")
                break
    if not isinstance(pack, dict):
        declared = benchmark.get("scenario_packs") or []
        if len(declared) == 1 and isinstance(declared[0], dict):
            pack = declared[0]
    if not isinstance(pack, dict):
        return None, None
    pack_id = pack.get("id")
    pack_version = pack.get("version")
    return (
        str(pack_id) if pack_id is not None else None,
        str(pack_version) if pack_version is not None else None,
    )


def compatibility_identity(
    summary: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    benchmark = summary["benchmark"]
    snapshot = benchmark.get("execution_snapshot") or {}
    agent = benchmark.get("agent") or {}
    pack_id, pack_version = scenario_pack(run, benchmark)
    return {
        "schema_version": 1,
        "suite": summary.get("suite"),
        "scenario": run.get("scenario"),
        "scenario_version": run.get("scenario_version"),
        "pack": {"id": pack_id, "version": pack_version},
        "harness_version": run.get("harness_version")
        or summary.get("harness_version"),
        "host_harness_sha256": snapshot.get("host_harness_sha256"),
        "scenario_pack_snapshots": snapshot.get("scenario_packs"),
        "fallback_replaybook_commit": (
            None
            if snapshot.get("host_harness_sha256")
            else benchmark.get("replaybook_commit")
        ),
        "benchmark_manifest": benchmark.get("benchmark_manifest"),
        "agent": {
            "name": agent.get("name") or run.get("agent"),
            "adapter": agent.get("adapter"),
            "adapter_sha256": snapshot.get("agent_adapter_sha256"),
            "payload_sha256": snapshot.get("agent_payload_sha256"),
        },
        "claux_release": benchmark.get("claux_release"),
        "claux_binary_sha256": snapshot.get("claux_binary_sha256"),
        "agent_timeout_seconds": run.get("agent_timeout_seconds")
        or benchmark.get("agent_timeout_seconds"),
    }


def optional_bool(value: Any) -> int | None:
    return int(value) if isinstance(value, bool) else None


def optional_number(value: Any, cast: type[int] | type[float]) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return cast(value)


def required_run_fields(run: dict[str, Any], path: Path) -> None:
    required = {
        "run_id",
        "scenario",
        "scenario_version",
        "agent",
        "model",
        "attempt",
        "reward",
    }
    missing = sorted(required - run.keys())
    if missing:
        raise CatalogError(f"{path}: run missing fields: {', '.join(missing)}")
    if run["reward"] not in (0, 1):
        raise CatalogError(f"{path}: {run['run_id']} has invalid reward")


def trial_status(run: dict[str, Any]) -> str:
    """Normalize runs written before trial_status was added to the harness."""
    value = run.get("trial_status")
    if isinstance(value, str):
        if value not in {"evaluated", "unavailable"}:
            raise CatalogError(
                f"{run.get('run_id', 'unknown run')} has unknown trial status: {value}"
            )
        return value
    return "evaluated"


def import_summary(connection: sqlite3.Connection, path: Path) -> ImportStats:
    summary = read_summary(path)
    if summary.get("suite") != SUPPORTED_SUITE:
        return ImportStats(skipped=1)
    benchmark = summary.get("benchmark")
    runs = summary.get("runs")
    if not isinstance(benchmark, dict) or not isinstance(runs, list):
        raise CatalogError(f"{path}: host matrix lacks benchmark or runs data")

    identity = matrix_identity(summary)
    matrix_id = content_hash(identity)
    snapshot = benchmark.get("execution_snapshot") or {}
    agent = benchmark.get("agent") or {}
    imported_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    harness_versions = summary.get("harness_versions") or [summary.get("harness_version")]
    errors = summary.get("infrastructure_errors") or []
    if not isinstance(errors, list):
        raise CatalogError(f"{path}: infrastructure_errors must be an array")

    with connection:
        connection.execute(
            """
            INSERT INTO matrices(
                matrix_id, source_path, source_sha256, suite, started_at,
                finished_at, replaybook_commit, harness_versions_json,
                benchmark_manifest_json, host_harness_sha256, agent_name,
                agent_adapter, claux_release, expected_trials, received_results,
                imported_at, benchmark_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(matrix_id) DO UPDATE SET
                source_path = excluded.source_path,
                source_sha256 = excluded.source_sha256,
                finished_at = excluded.finished_at,
                expected_trials = excluded.expected_trials,
                received_results = excluded.received_results,
                imported_at = excluded.imported_at,
                benchmark_json = excluded.benchmark_json
            """,
            (
                matrix_id,
                str(path.resolve()),
                file_hash(path),
                summary["suite"],
                summary.get("started_at"),
                summary.get("finished_at"),
                benchmark.get("replaybook_commit"),
                canonical_json(harness_versions),
                (
                    canonical_json(benchmark["benchmark_manifest"])
                    if isinstance(benchmark.get("benchmark_manifest"), dict)
                    else None
                ),
                snapshot.get("host_harness_sha256"),
                agent.get("name"),
                agent.get("adapter"),
                benchmark.get("claux_release"),
                summary.get("expected_trials"),
                summary.get("received_results"),
                imported_at,
                canonical_json(benchmark),
            ),
        )
        connection.execute("DELETE FROM trials WHERE matrix_id = ?", (matrix_id,))
        connection.execute("DELETE FROM matrix_errors WHERE matrix_id = ?", (matrix_id,))

        for run in runs:
            if not isinstance(run, dict):
                raise CatalogError(f"{path}: every run must be an object")
            required_run_fields(run, path)
            compatibility = compatibility_identity(summary, run)
            compatibility_id = content_hash(compatibility)
            pack_id, pack_version = scenario_pack(run, benchmark)
            usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
            verification = (
                run.get("verification")
                if isinstance(run.get("verification"), dict)
                else {}
            )
            after_timeout = verification.get("after_agent_timeout")
            durable_after_timeout = int(
                isinstance(after_timeout, dict)
                and after_timeout.get("durable_repair") is True
            )
            try:
                recording = compact_recording(run.get("recording"), run["run_id"], path)
            except PublishError as error:
                raise CatalogError(str(error)) from error
            recording = recording or {}
            trial_id = hashlib.sha256(
                f"{matrix_id}\0{run['run_id']}".encode()
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO trials(
                    trial_id, matrix_id, compatibility_id, compatibility_json,
                    run_id, scenario, scenario_version, pack_id, pack_version,
                    harness_version, agent, agent_adapter, model,
                    reasoning_effort, attempt, started_at, finished_at,
                    duration_seconds, timeout_seconds, trial_status, reward,
                    failure_category, failure, input_tokens, output_tokens,
                    cache_read_tokens, cache_creation_tokens, cost_usd,
                    immediate_http, service_restart, host_reboot,
                    durable_after_timeout, model_rounds, model_duration_seconds,
                    tool_calls, tool_duration_seconds,
                    first_non_read_only_seconds,
                    post_first_non_read_only_seconds, result_path,
                    transcript_path, log_path, raw_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    trial_id,
                    matrix_id,
                    compatibility_id,
                    canonical_json(compatibility),
                    run["run_id"],
                    run["scenario"],
                    run["scenario_version"],
                    pack_id,
                    pack_version,
                    run.get("harness_version") or summary.get("harness_version"),
                    run["agent"],
                    agent.get("adapter"),
                    run["model"],
                    run.get("reasoning_effort"),
                    run["attempt"],
                    run.get("started_at"),
                    run.get("finished_at"),
                    optional_number(run.get("agent_duration_seconds"), float),
                    optional_number(run.get("agent_timeout_seconds"), int),
                    trial_status(run),
                    run["reward"],
                    run.get("failure_category"),
                    run.get("failure"),
                    optional_number(usage.get("input_tokens"), int),
                    optional_number(usage.get("output_tokens"), int),
                    optional_number(usage.get("cache_read_tokens"), int),
                    optional_number(usage.get("cache_creation_tokens"), int),
                    optional_number(usage.get("cost_usd"), float),
                    optional_bool(verification.get("immediate_http")),
                    optional_bool(verification.get("service_restart")),
                    optional_bool(verification.get("host_reboot")),
                    durable_after_timeout,
                    optional_number(recording.get("model_rounds"), float),
                    optional_number(recording.get("model_duration_seconds"), float),
                    optional_number(recording.get("tool_calls"), float),
                    optional_number(recording.get("tool_duration_seconds"), float),
                    optional_number(
                        recording.get("first_non_read_only_tool_seconds"), float
                    ),
                    optional_number(
                        recording.get("post_first_non_read_only_seconds"), float
                    ),
                    run.get("result_file"),
                    run.get("transcript_file"),
                    run.get("log_file"),
                    canonical_json(run),
                ),
            )

        for error in errors:
            if not isinstance(error, dict) or not isinstance(error.get("run_id"), str):
                raise CatalogError(f"{path}: malformed infrastructure error")
            connection.execute(
                """
                INSERT INTO matrix_errors(matrix_id, run_id, exit_code, error, log_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    matrix_id,
                    error["run_id"],
                    error.get("exit_code"),
                    error.get("error"),
                    error.get("log_file"),
                ),
            )

    return ImportStats(matrices=1, trials=len(runs), errors=len(errors))


def import_paths(connection: sqlite3.Connection, inputs: Iterable[Path]) -> ImportStats:
    total = ImportStats()
    for path in summary_paths(inputs):
        total = total.add(import_summary(connection, path))
    return total


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    rounded = round(seconds)
    return f"{rounded // 60}:{rounded % 60:02d}"


def format_money(value: float | None, incomplete: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"${value:.4f}{'+' if incomplete else ''}"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(cell.ljust(widths[index]) for index, cell in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))
        for row in rows
    )
    return "\n".join(lines)


def compatibility_cohorts(
    connection: sqlite3.Connection,
    scenario: str,
    scenario_version: int | None = None,
) -> list[sqlite3.Row]:
    conditions = ["trials.scenario = ?"]
    values: list[Any] = [scenario]
    if scenario_version is not None:
        conditions.append("trials.scenario_version = ?")
        values.append(scenario_version)
    return connection.execute(
        f"""
        SELECT
            compatibility_id,
            compatibility_json,
            MAX(COALESCE(trials.started_at, matrices.started_at, '')) AS newest,
            COUNT(*) AS trials
        FROM trials
        JOIN matrices USING(matrix_id)
        WHERE {' AND '.join(conditions)}
        GROUP BY compatibility_id, compatibility_json
        ORDER BY newest DESC, compatibility_id
        """,
        values,
    ).fetchall()


def resolve_cohort(cohorts: list[sqlite3.Row], supplied: str | None) -> sqlite3.Row:
    if not cohorts:
        raise CatalogError("no matching trials in the catalog")
    if supplied is None:
        return cohorts[0]
    matches = [row for row in cohorts if row["compatibility_id"].startswith(supplied)]
    if not matches:
        raise CatalogError(f"unknown compatibility cohort: {supplied}")
    if len(matches) > 1:
        raise CatalogError(f"ambiguous compatibility cohort prefix: {supplied}")
    return matches[0]


def aggregate_model_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str | None], list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((row["model"], row["reasoning_effort"]), []).append(row)
    aggregates = []
    for (model, reasoning), trials in grouped.items():
        evaluated = [row for row in trials if row["trial_status"] == "evaluated"]
        passed = [row for row in evaluated if row["reward"] == 1]
        costs = [float(row["cost_usd"]) for row in trials if row["cost_usd"] is not None]
        durations = [
            float(row["duration_seconds"])
            for row in evaluated
            if row["duration_seconds"] is not None
        ]
        known_cost = sum(costs)
        interval = wilson_interval(len(passed), len(evaluated))
        aggregates.append(
            {
                "model": model,
                "reasoning": reasoning,
                "trials": len(trials),
                "evaluated": len(evaluated),
                "unavailable": len(trials) - len(evaluated),
                "passed": len(passed),
                "failed": len(evaluated) - len(passed),
                "pass_rate": len(passed) / len(evaluated) if evaluated else None,
                "interval": interval,
                "median": statistics.median(durations) if durations else None,
                "cost": known_cost,
                "cost_complete": len(costs) == len(trials),
                "cost_per_repair": known_cost / len(passed) if passed else None,
                "input_tokens": sum(row["input_tokens"] or 0 for row in trials),
                "output_tokens": sum(row["output_tokens"] or 0 for row in trials),
            }
        )
    return sorted(
        aggregates,
        key=lambda item: (
            -(item["pass_rate"] if item["pass_rate"] is not None else -1),
            (
                item["cost_per_repair"]
                if item["cost_per_repair"] is not None
                else float("inf")
            ),
            item["model"],
            item["reasoning"] or "",
        ),
    )


def compare(
    connection: sqlite3.Connection,
    scenario: str,
    scenario_version: int | None = None,
    compatibility: str | None = None,
) -> str:
    cohorts = compatibility_cohorts(connection, scenario, scenario_version)
    cohort = resolve_cohort(cohorts, compatibility)
    identity = json.loads(cohort["compatibility_json"])
    rows = connection.execute(
        """
        SELECT * FROM trials
        WHERE scenario = ? AND compatibility_id = ?
        ORDER BY model, reasoning_effort, started_at
        """,
        (scenario, cohort["compatibility_id"]),
    ).fetchall()
    aggregates = aggregate_model_rows(rows)
    table_rows = []
    for item in aggregates:
        interval = item["interval"]
        table_rows.append(
            [
                item["model"],
                item["reasoning"] or "default",
                str(item["trials"]),
                str(item["evaluated"]),
                str(item["unavailable"]),
                str(item["passed"]),
                str(item["failed"]),
                (
                    f"{item['pass_rate'] * 100:.0f}%"
                    if item["pass_rate"] is not None
                    else "n/a"
                ),
                (
                    f"{interval[0] * 100:.0f}-{interval[1] * 100:.0f}%"
                    if interval is not None
                    else "n/a"
                ),
                format_duration(item["median"]),
                format_money(item["cost"], not item["cost_complete"]),
                format_money(
                    item["cost_per_repair"], not item["cost_complete"]
                ),
            ]
        )
    pack = identity.get("pack") or {}
    agent = identity.get("agent") or {}
    title = (
        f"scenario: {identity['scenario']} v{identity['scenario_version']}\n"
        f"compatibility: {cohort['compatibility_id'][:12]}\n"
        f"pack: {pack.get('id') or 'n/a'}@{pack.get('version') or 'n/a'}\n"
        f"host harness: v{identity.get('harness_version') or 'n/a'}\n"
        f"agent: {agent.get('name') or 'n/a'} / {agent.get('adapter') or 'n/a'}\n"
        f"claux: {identity.get('claux_release') or 'n/a'}"
    )
    if len(cohorts) > 1 and compatibility is None:
        title += (
            f"\ncohorts: using newest of {len(cohorts)}; "
            "pass --compatibility <id> to select another"
        )
    infrastructure_errors = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM matrix_errors
        WHERE matrix_id IN (
            SELECT DISTINCT matrix_id FROM trials
            WHERE scenario = ? AND compatibility_id = ?
        ) AND run_id LIKE ?
        """,
        (scenario, cohort["compatibility_id"], f"{scenario}-%"),
    ).fetchone()["count"]
    if infrastructure_errors:
        title += f"\ninfrastructure errors: {infrastructure_errors} (excluded from rates)"
    return title + "\n\n" + render_table(
        [
            "model",
            "reasoning",
            "trials",
            "eval",
            "unavail",
            "passed",
            "failed",
            "pass",
            "95% CI",
            "median",
            "cost",
            "cost/repair",
        ],
        table_rows,
    )


def cohort_listing(
    connection: sqlite3.Connection,
    scenario: str,
    scenario_version: int | None = None,
) -> str:
    cohorts = compatibility_cohorts(connection, scenario, scenario_version)
    if not cohorts:
        raise CatalogError("no matching trials in the catalog")
    rows = []
    for cohort in cohorts:
        identity = json.loads(cohort["compatibility_json"])
        pack = identity.get("pack") or {}
        rows.append(
            [
                cohort["compatibility_id"][:12],
                str(cohort["trials"]),
                cohort["newest"] or "n/a",
                f"v{identity.get('scenario_version')}",
                f"{pack.get('id') or 'n/a'}@{pack.get('version') or 'n/a'}",
                f"v{identity.get('harness_version') or 'n/a'}",
                identity.get("claux_release") or "n/a",
            ]
        )
    return render_table(
        ["compatibility", "trials", "newest", "scenario", "pack", "harness", "claux"],
        rows,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"derived SQLite catalog (default: {DEFAULT_DATABASE})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="import host-matrix summaries")
    importer.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[REPO_DIR / "jobs"],
        help="summary.json files or directories to scan (default: jobs/)",
    )
    comparer = commands.add_parser("compare", help="compare one compatibility cohort")
    comparer.add_argument("--scenario", required=True)
    comparer.add_argument("--scenario-version", type=int)
    comparer.add_argument("--compatibility")
    comparer.add_argument("--list-cohorts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        connection = connect(args.database)
        with connection:
            if args.command == "import":
                stats = import_paths(connection, args.paths)
                print(
                    f"imported {stats.matrices} matrices, {stats.trials} trials, "
                    f"and {stats.errors} infrastructure errors; "
                    f"skipped {stats.skipped} unsupported summaries"
                )
                print(f"catalog: {args.database.expanduser().resolve()}")
            elif args.list_cohorts:
                print(
                    cohort_listing(connection, args.scenario, args.scenario_version)
                )
            else:
                print(
                    compare(
                        connection,
                        args.scenario,
                        args.scenario_version,
                        args.compatibility,
                    )
                )
        connection.close()
    except (CatalogError, OSError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
