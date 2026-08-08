from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from integrations.host.run_host_matrix import (
    Job,
    WorkerResult,
    build_jobs,
    build_summary,
    discover_scenarios,
    run_jobs,
    slugify,
)


class HostMatrixTests(unittest.TestCase):
    def test_discovers_versioned_host_scenarios(self) -> None:
        scenarios = discover_scenarios()
        self.assertEqual(scenarios["001-nginx-502-host"], 1)
        self.assertEqual(scenarios["013-sidekiq-wrong-redis"], 2)
        self.assertEqual(scenarios["014-missing-rails-migration"], 2)
        self.assertEqual(scenarios["015-sidekiq-poison-pill"], 1)
        self.assertEqual(scenarios["016-rails-pool-exhaustion"], 1)

    def test_typed_manifest_takes_precedence_over_legacy_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario_dir = Path(temporary) / "typed"
            scenario_dir.mkdir()
            (scenario_dir / "scenario.toml").write_text("[scenario]\nversion = 3\n")
            (scenario_dir / "scenario.conf").write_text('SCENARIO_VERSION="1"\n')
            self.assertEqual(discover_scenarios(Path(temporary)), {"typed": 3})

    def test_build_jobs_assigns_stable_adjacent_port_pairs(self) -> None:
        jobs = build_jobs(
            scenarios=["013-sidekiq-wrong-redis"],
            models=["vendor/model-a", "vendor/model-b"],
            attempts=2,
            base_port=23000,
            matrix_dir=Path("/tmp/matrix"),
        )
        self.assertEqual(len(jobs), 4)
        self.assertEqual(
            [(job.ssh_port, job.http_port) for job in jobs],
            [(23000, 23001), (23002, 23003), (23004, 23005), (23006, 23007)],
        )
        self.assertEqual(
            jobs[0].run_id,
            "013-sidekiq-wrong-redis-vendor-model-a-1",
        )

    def test_build_jobs_rejects_slug_collisions(self) -> None:
        with self.assertRaisesRegex(ValueError, "colliding run IDs"):
            build_jobs(
                scenarios=["013-sidekiq-wrong-redis"],
                models=["vendor/model.a", "vendor/model-a"],
                attempts=1,
                base_port=23000,
                matrix_dir=Path("/tmp/matrix"),
            )

    def test_summary_keeps_evaluation_failures_as_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs = build_jobs(
                scenarios=["013-sidekiq-wrong-redis"],
                models=["pass/model", "fail/model"],
                attempts=1,
                base_port=23000,
                matrix_dir=root,
            )
            workers = []
            for job, reward, category in (
                (jobs[0], 1, None),
                (jobs[1], 0, "backlog_not_recovered"),
            ):
                result = {
                    "harness_version": 5,
                    "scenario": job.scenario,
                    "scenario_version": 2,
                    "model": job.model,
                    "reward": reward,
                    "failure_category": category,
                    "agent_duration_seconds": 60,
                    "usage": {"cost_usd": 0.01},
                }
                workers.append(WorkerResult(job, 0 if reward else 1, result, None))

            summary = build_summary(
                workers,
                started_at="2026-08-07T00:00:00Z",
                benchmark={"suite": "test"},
            )

        self.assertEqual(summary["received_results"], 2)
        self.assertEqual(summary["harness_version"], 5)
        self.assertEqual(summary["totals"]["evaluated"], 2)
        self.assertEqual(summary["totals"]["unavailable"], 0)
        self.assertEqual(summary["totals"]["passed"], 1)
        self.assertEqual(summary["totals"]["failed"], 1)
        self.assertEqual(summary["totals"]["input_tokens"], 0)
        self.assertEqual(summary["totals"]["usage_reported_trials"], 2)
        self.assertEqual(summary["infrastructure_errors"], [])
        self.assertEqual(
            summary["failure_categories"],
            [{"category": "backlog_not_recovered", "count": 1}],
        )
        self.assertEqual(summary["unavailable_categories"], [])

    def test_summary_excludes_unavailable_trials_from_pass_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs = build_jobs(
                scenarios=["014-missing-rails-migration"],
                models=["pass/model", "unavailable/model"],
                attempts=1,
                base_port=23000,
                matrix_dir=root,
            )
            passed = {
                "harness_version": 5,
                "scenario": jobs[0].scenario,
                "scenario_version": 2,
                "model": jobs[0].model,
                "reward": 1,
                "trial_status": "evaluated",
                "failure_category": None,
                "agent_duration_seconds": 120,
                "usage": {"input_tokens": 100, "cost_usd": 0.01},
            }
            unavailable = {
                "harness_version": 5,
                "scenario": jobs[1].scenario,
                "scenario_version": 2,
                "model": jobs[1].model,
                "reward": 0,
                "trial_status": "unavailable",
                "failure_category": "provider_unavailable",
                "agent_duration_seconds": 4,
                "usage": {"input_tokens": 0, "cost_usd": None},
            }
            workers = [
                WorkerResult(jobs[0], 0, passed, None),
                WorkerResult(jobs[1], 1, unavailable, None),
            ]
            summary = build_summary(
                workers,
                started_at="2026-08-08T00:00:00Z",
                benchmark={"suite": "test"},
            )

        self.assertEqual(summary["totals"]["trials"], 2)
        self.assertEqual(summary["totals"]["evaluated"], 1)
        self.assertEqual(summary["totals"]["unavailable"], 1)
        self.assertEqual(summary["totals"]["passed"], 1)
        self.assertEqual(summary["totals"]["failed"], 0)
        self.assertEqual(summary["totals"]["pass_rate"], 1.0)
        self.assertEqual(summary["totals"]["median_duration_seconds"], 120)
        self.assertEqual(summary["failure_categories"], [])
        self.assertEqual(
            summary["unavailable_categories"],
            [{"category": "provider_unavailable", "count": 1}],
        )
        unavailable_row = next(
            row for row in summary["by_model"] if row["model"] == "unavailable/model"
        )
        self.assertEqual(unavailable_row["evaluated"], 0)
        self.assertEqual(unavailable_row["unavailable"], 1)
        self.assertIsNone(unavailable_row["pass_rate"])
        self.assertIsNone(unavailable_row["median_duration_seconds"])

    def test_worker_accepts_nonzero_evaluation_with_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = root / "fake-runner.sh"
            runner.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
output=""
model="oracle"
scenario=""
agent_timeout=""
while (( $# > 0 )); do
  case "$1" in
    --output-dir) output="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --scenario) scenario="$2"; shift 2 ;;
    --agent-timeout-seconds) agent_timeout="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$output"
python - "$output/result.json" "$scenario" "$model" "$agent_timeout" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
result = {
    "harness_version": 5,
    "scenario": sys.argv[2],
    "scenario_version": 2,
    "model": sys.argv[3],
    "reward": 0,
    "failure_category": "backlog_not_recovered",
    "test_agent_timeout_seconds": int(sys.argv[4]),
}
path.write_text(json.dumps(result))
PY
exit 1
"""
            )
            job = Job(
                run_id="test-run",
                scenario="013-sidekiq-wrong-redis",
                model="vendor/model",
                attempt=1,
                ssh_port=23000,
                http_port=23001,
                output_dir=root / "run",
                log_file=root / "run.log",
            )
            results = asyncio.run(
                run_jobs(
                    [job],
                    runner=runner,
                    environment={},
                    concurrency=1,
                    agent_timeout_seconds=321,
                )
            )

        self.assertEqual(results[0].exit_code, 1)
        self.assertIsNotNone(results[0].result)
        self.assertIsNone(results[0].error)
        self.assertEqual(results[0].result["test_agent_timeout_seconds"], 321)

    def test_worker_passes_custom_agent_contract_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = root / "fake-runner.sh"
            runner.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
output=""
model=""
scenario=""
adapter=""
payload=""
env_file=""
agent_name=""
while (( $# > 0 )); do
  case "$1" in
    --output-dir) output="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --scenario) scenario="$2"; shift 2 ;;
    --agent-adapter) adapter="$2"; shift 2 ;;
    --agent-payload) payload="$2"; shift 2 ;;
    --agent-env-file) env_file="$2"; shift 2 ;;
    --agent-name) agent_name="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$output"
python - "$output/result.json" "$scenario" "$model" "$adapter" "$payload" "$env_file" "$agent_name" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "harness_version": 5,
    "scenario": sys.argv[2],
    "scenario_version": 2,
    "model": sys.argv[3],
    "reward": 1,
    "adapter_args": sys.argv[4:],
}))
PY
"""
            )
            job = Job(
                run_id="adapter-run",
                scenario="013-sidekiq-wrong-redis",
                model="vendor/model",
                attempt=1,
                ssh_port=23000,
                http_port=23001,
                output_dir=root / "run",
                log_file=root / "run.log",
            )
            adapter = root / "adapter"
            payload = root / "payload"
            env_file = root / "agent.env"
            results = asyncio.run(
                run_jobs(
                    [job],
                    runner=runner,
                    environment={},
                    concurrency=1,
                    agent_adapter=adapter,
                    agent_payload=payload,
                    agent_env_file=env_file,
                    agent_name="my-agent",
                )
            )

        self.assertEqual(
            results[0].result["adapter_args"],
            [str(adapter), str(payload), str(env_file), "my-agent"],
        )

    def test_slugify_model_id(self) -> None:
        self.assertEqual(slugify("OpenAI/GPT-5.6 Luna"), "openai-gpt-5-6-luna")


if __name__ == "__main__":
    unittest.main()
