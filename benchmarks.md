# Replaybook benchmarks

Replaybook evaluates whether an agent can diagnose and durably repair a running
system. A passing health check is not enough. The verifier restarts the affected
services and checks the user-facing behavior again.

These results are evaluation history, not a definitive model ranking. Runs made
with different scenario sets, verifier versions, agent harnesses, or attempt
counts should not be compared as if they were one controlled experiment.

## Development baseline

### DeepSeek V4 Flash, 2026-08-06

This is the pre-improvement baseline for the six-scenario development set. Each
scenario received one attempt through Claux using
`deepseek/deepseek-v4-flash`.

| Scenario | Durable repair | Duration | Reported cost | Notes |
|---|---:|---:|---:|---|
| 001 Nginx 502 | No | 5:00 | $0.0056 | Replaced a Compose-managed service with `docker run` |
| 002 PostgreSQL rejecting connections | Yes | 3:36 | $0.0070 | Removed the rejecting `pg_hba.conf` rule |
| 004 Disk full | Yes | 2:32 | $0.0034 | Removed the source of temporary filesystem exhaustion |
| 007 Packet loss | Yes | 4:20 | $0.0079 | Removed the live traffic impairment durably |
| 010 Stale authentication secret | Yes | 3:08 | $0.0065 | Preserved authentication across restart |
| 012 Retry storm | Yes* | 9:17 | $0.0322 | Recreated the app manually with copied Compose labels |
| **Total** | **5/6 reported** | **3:58 median** | **$0.0627** | **No agent errors** |

The Nginx failure is useful agent-behavior data. DeepSeek diagnosed the port
mismatch but removed the Compose-managed application and recreated it with raw
`docker run`. The endpoint recovered, but the verifier could no longer identify
the expected deployment topology.

The retry-storm pass exposed a verifier weakness. DeepSeek also recreated that
application with raw `docker run`, but copied three Compose labels onto the new
container. The verifier accepted those labels as evidence that the service was
still Compose-managed. The reported score remains recorded above, but this run
should not be treated as a clean durable repair until topology verification is
strengthened.

Run metadata:

- Scenario set: `development`
- Benchmark suite: `replaybook-harbor-v1`
- Attempts per scenario: `1`
- Replaybook commit: `ff4965752535994c994fad9dec05532843355b1a`
- Claux release: `20260804.0.0`
- Reported input tokens: `2,068,395`
- Reported cached tokens: `1,807,744`
- Output tokens: `39,319`

The six-scenario development result is deliberately not a leaderboard entry.
It is the baseline used to improve prompts, tools, and agent policy. The held-out
set remains reserved for measuring whether those changes generalize.

New runs use `replaybook-harbor-v2`. Version 2 strengthens retry-storm topology
verification to reject raw container replacements that only imitate a subset
of Compose metadata, so its results should not be merged with version 1 scores.

## Historical comparisons

These runs predate the fixed development and held-out split. They remain useful
records, but should not be combined with the current baseline.

### Original three-scenario agent comparison

One 27-trial matrix across the original three scenarios produced:

| Agent | Durable repairs | Median trial time | Reported cost |
|---|---:|---:|---:|
| Codex / GPT-5.6 Sol | 9/9 | 3:35 | unavailable |
| Claude Code / Sonnet 5 | 8/9 | 3:15 | $2.30 |
| Claux / DeepSeek V4 Flash | 8/9 | 3:15 | $0.07 |
| **Total** | **25/27** | | **$2.37 known** |

Both misses were on the Nginx scenario. One repair did not survive the service
restart. The other replaced the managed app container, so the verifier could
no longer identify and restart the deployed topology. Those are agent-behavior
signals, not setup failures. Codex cost is unavailable, not zero.

### Six-scenario OpenRouter comparison

Three attempts per scenario produced:

| Model | Durable repairs | Agent errors | Median trial time | Reported cost |
|---|---:|---:|---:|---:|
| DeepSeek V4 Flash | 17/18 | 0 | 3:16 | $0.12 |
| GPT-5.6 Luna | 15/18 | 0 | 2:36 | $0.07 |
| Poolside Laguna S 2.1 | 14/18 | 2 | 3:45 | $0.24 known |

Luna completed every scenario except Nginx. In all three misses it diagnosed
the port mismatch correctly, started the app on the expected port, and passed
the immediate health check. The repair existed only in the running process, so
the verifier's restart restored the broken service command. Laguna's reported
cost excludes two timed-out attempts that did not return usage.

### Nine-scenario OpenRouter comparison

DeepSeek V4 Flash and MiniMax M3 each ran every scenario three times through
the same Claux adapter and restart verifier:

| Model | Durable repairs | Agent errors | Median trial time | Reported cost |
|---|---:|---:|---:|---:|
| DeepSeek V4 Flash | 27/27 | 0 | 3:39 | $0.20 |
| MiniMax M3 | 24/27 | 0 | 3:12 | $0.64 |

MiniMax was 27 seconds faster at the median, but made three distinct durability
mistakes. On Nginx it started a second server that disappeared during restart.
On the missing environment variable scenario it diagnosed the export issue,
then replaced the Compose-managed container and broke the expected service
topology. On packet loss it removed the live impairment and then deleted the
sentinel whose presence prevented the startup script from injecting the fault
again. All three trials completed normally; the failed rewards came from the
verifier rejecting the repairs.

## Publishing comparable results

A result should only be promoted to a leaderboard-style comparison when it has:

- Frozen scenario and verifier versions
- A held-out scenario set
- At least three attempts per model and scenario
- No known verifier correctness problems
- Exact agent, model, prompt or skill, and tool versions
- Cost and duration coverage
- Sanitized result artifacts suitable for inspection

## Reproducing runs

Run a development baseline with:

```nu
nu integrations/harbor/run-isolated-matrix.nu \
  --scenario-set development \
  --agent claux \
  --claux-model deepseek/deepseek-v4-flash \
  --attempts 1
```

Inspect captured trajectories with:

```sh
integrations/harbor/analyze_trajectory.py \
  jobs/isolated-matrix-YYYY-MM-DD__HH-MM-SS.XXXXXX
```

See [`integrations/harbor/README.md`](integrations/harbor/README.md) for task
definitions, worker setup, matrix comparison, and result inspection.

For a deeper explanation of restart-based verification, see [Evaluating
Infrastructure Agents in Running Systems](https://jakegoldsborough.com/blog/2026/evaluating-infrastructure-agents-in-running-systems/).
