# Replaybook benchmarks

Replaybook evaluates whether an agent can diagnose and durably repair a running
system. A passing health check is not enough. The verifier restarts the affected
services and checks the user-facing behavior again.

These results are evaluation history, not a definitive model ranking. Runs made
with different scenario sets, verifier versions, agent harnesses, or attempt
counts should not be compared as if they were one controlled experiment.

<!-- replaybook:current-benchmark:start -->
## Four models on the declarative host suite

Four models ran the same five declarative infrastructure incidents three times each under host harness v6. Every passing repair survived scenario verification, service restart, and host reboot.

Benchmark release: `20260809.0.3`

| Model | Durable repairs | Pass rate | Median | Known cost | Cost per repair |
|---|---:|---:|---:|---:|---:|
| GLM 5.2 | 15/15 | 100% | 1:34 | $0.3021 | $0.0201 |
| GPT-5.6 Luna | 13/15 | 87% | 1:10 | $0.0986 | $0.0076 |
| DeepSeek V4 Flash 0731 | 13/15 | 87% | 2:28 | $0.2908 | $0.0224 |
| Tencent HY3 Preview | 12/15 | 80% | 2:14 | $0.1050 | $0.0087 |
| **Total** | **53/60** | **88%** | **1:56** | **$0.7964** | **$0.0150** |

GLM 5.2 was the only model to make 15 durable repairs in 15 attempts. Its median was 1:34 and its $0.3021 total cost worked out to about $0.0201 per repair.

GPT-5.6 Luna was fastest at the median, 1:10, and cheapest per durable repair at about $0.0076. It made 13 repairs in 15 attempts, losing existing backlog once and timing out once on the poison-pill incident.

DeepSeek V4 Flash 0731 also finished 13/15, but its 2:28 median and $0.2908 total cost were both higher than Luna's. Both DeepSeek failures were poison-pill timeouts after substantial repair work.

Tencent HY3 Preview made 12 repairs in 15 attempts for $0.1050. Its three failures were stateful: two lost pre-existing Sidekiq backlog and one did not quarantine poison work safely.

All four models passed every Nginx, Rails migration, and Rails pool-exhaustion attempt. All seven failures occurred in the two Sidekiq scenarios that require preserving or quarantining existing work, rather than merely restoring a healthy endpoint.

These are 60 controlled trials from one harness generation, not a universal model ranking. Three attempts per model and scenario remain a small sample, but the failures show why durable state verification changes the comparison.

### Scenario breakdown

| Scenario | Version | GLM 5.2 | GPT-5.6 Luna | DeepSeek V4 Flash 0731 | Tencent HY3 Preview |
|---|---:|---:|---:|---:|---:|
| Nginx 502 | v1 | 3/3, 0:32 | 3/3, 0:40 | 3/3, 1:30 | 3/3, 1:30 |
| Sidekiq wrong Redis database | v2 | 3/3, 1:15 | 2/3, 0:56 | 3/3, 2:28 | 1/3, 2:13 |
| Missing Rails migration | v2 | 3/3, 1:34 | 3/3, 1:15 | 3/3, 2:26 | 3/3, 1:55 |
| Sidekiq poison pill | v1 | 3/3, 5:21 | 2/3, 1:42 | 1/3, 15:04 | 2/3, 5:40 |
| Rails pool exhaustion | v1 | 3/3, 3:58 | 3/3, 1:11 | 3/3, 3:56 | 3/3, 5:01 |

### Failure categories

- `agent_timeout`: 3
- `backlog_not_recovered`: 3
- `poison_not_quarantined`: 1

### Source matrices

- `host-matrix-2026-08-09__17-21-52.06cac8`: deepseek/deepseek-v4-flash-0731; Replaybook `f949410f`
- `host-matrix-2026-08-09__18-49-02.adba93`: openai/gpt-5.6-luna, z-ai/glm-5.2, tencent/hy3-preview; Replaybook `5a7f6a7e`

### Run notes

- Two original GLM Nginx workers were interrupted when the checked-out runner changed during the active matrix. Their incomplete artifacts were preserved and excluded. The two replacement trials used the original harness v6 runner, model, scenario, ports, timeout, adapter, and Claux release.

<!-- replaybook:current-benchmark:end -->

## Host-native harness v2 baseline

### DeepSeek V4 Flash 0731, 2026-08-08

This is the first baseline produced by host harness version 2. The controller
boots a disposable NixOS VM, gives the agent the incident instruction, and
keeps the reference repair and verifier outside the model VM. Each repair must
pass immediately, after the affected services restart, and after the host
reboots. Stateful scenarios also require the exact pre-existing jobs to
complete.

DeepSeek V4 Flash 0731 ran each of the three host-native scenarios three times:

| Scenario | Version | Durable repairs | Median trial time | Reported cost |
|---|---:|---:|---:|---:|
| 001 Nginx 502 | 1 | 3/3 | 0:55 | $0.0093 |
| 013 Sidekiq wrong Redis database | 2 | 3/3 | 2:02 | $0.0146 |
| 014 Missing Rails migration | 2 | 3/3 | 4:24 | $0.0353 |
| **Total** | | **9/9** | **2:02 median** | **$0.0592** |

All nine trials returned complete usage data. Together they used 1,703,055
input tokens, 1,428,352 cached input tokens, and 44,979 output tokens. No trial
timed out, changed the expected service topology, lost pre-existing work, or
failed restart or reboot verification. The captured tool traces contain no
references to the oracle or verifier.

The migration scenario accounted for about 61% of input tokens and 60% of the
reported cost. Its 4:24 median remains meaningfully slower than the other two
scenarios, so the current suite still distinguishes a longer stateful repair
from the simpler Nginx incident.

Run metadata:

- Host harness version: `2`
- Benchmark suite: `replaybook-host-matrix-v1`
- Attempts per scenario: `3`
- Replaybook commit: `3ea781c9ce030ecbcb4e84a0b45389b67fa674c9`
- Claux release: `v20260808.0.0`
- Agent timeout: `900` seconds
- Concurrency: `2`

Reproduce this baseline with:

```sh
python integrations/host/run_host_matrix.py \
  --scenario 001-nginx-502-host \
  --scenario 013-sidekiq-wrong-redis \
  --scenario 014-missing-rails-migration \
  --models deepseek/deepseek-v4-flash-0731 \
  --attempts 3 \
  --concurrency 2
```

### Controlled DeepSeek revision comparison

The original `deepseek/deepseek-v4-flash` model then ran through the same
Replaybook commit, host harness, scenario versions, Claux release, timeout,
concurrency, and three-attempt structure. The model ID was the only intentional
evaluation variable.

| Metric | Original V4 Flash | V4 Flash 0731 | Change |
|---|---:|---:|---:|
| Durable repairs | 8/9 | 9/9 | +1 pass |
| Median trial time | 2:13 | 2:02 | 11 seconds faster |
| Input tokens | 3,488,467 | 1,703,055 | 51% fewer |
| Output tokens | 64,448 | 44,979 | 30% fewer |
| Reported cost | $0.0811 | $0.0592 | 27% lower |

Original V4 Flash passed Nginx and Sidekiq three times each, then passed the
migration scenario twice in three attempts. In the failed attempt it diagnosed
the missing `delivery_state` column and added that column manually, but never
applied or recorded the deployed migration. The live schema partially
recovered, while `/deployment/migration` still reported the migration missing.
The verifier rejected the repair as `migration_not_applied`.

V4 Flash 0731 applied the complete repair in all three migration attempts. It
also used about 60% fewer input tokens on that scenario. The newer revision was
not uniformly faster: original V4 Flash had a 1:51 Sidekiq median versus 2:02
for 0731. Across the complete nine-trial matrix, however, 0731 was faster at the
median, substantially more token-efficient, cheaper, and more reliable.

Three attempts per scenario is still a small sample. The one-pass reliability
difference is encouraging rather than definitive. The 51% input-token reduction
is the stronger signal and is large enough to justify using 0731 as the current
Claux default while the benchmark grows.

## Host-native Sidekiq incident

### Version 2: backlog preservation

Version 2 of `013-sidekiq-wrong-redis` seeds three checkout confirmation jobs
before the agent begins. The controller retains their exact IDs and requires
all three to reach PostgreSQL. It then submits new jobs after the repair,
service restart, and host reboot.

### Archived harness v1 matrix results

These August 7, 2026 runs used the Python host-native matrix runner and version
2 of the scenario. DeepSeek, Laguna, Luna, and MiniMax ran together in one
12-trial batch. Qwen ran separately immediately afterward with the same Claux
harness, scenario, verifier, and attempt count.

Host harness v1 copied the reference repair into the model VM. The trajectories
remain useful development records, but the harness did not guarantee answer-key
isolation. These results are archived and must not be presented as a controlled
model ranking or compared directly with the harness-v2 baseline.

| Model | Durable repairs | Median trial time | Reported cost |
|---|---:|---:|---:|
| DeepSeek V4 Flash | 3/3 | 1:31 | $0.0187 |
| Poolside Laguna S 2.1 | 3/3 | 0:57 | $0.0133 |
| MiniMax M3 | 3/3 | 1:49 | $0.1348 |
| Qwen3.8 Max | 3/3 | 4:52 | $0.5816 |
| GPT-5.6 Luna | 2/3 | 1:12 | $0.0236 |

Qwen recovered every seeded job, but used 783,176 input tokens and 38,411
output tokens across its three attempts. It was more than three times slower
and roughly 31 times more expensive than DeepSeek on this scenario. The extra
work did not improve the score because the cheaper models already reached the
verifier's ceiling.

Luna's failed attempt repaired the live service and processed new jobs, but did
not recover all three controller-owned backlog IDs. The runner recorded it as
`backlog_not_recovered`, an evaluation failure rather than an infrastructure
error. All 15 trials produced valid results.

These are still development results from one scenario, not a general model
ranking. Qwen's trials were a separate batch, so small differences in host or
provider conditions may affect timing.

### Earlier single-attempt results

These single development attempts from August 7, 2026 demonstrate the verifier
behavior that motivated the stricter backlog check.

| Model | Durable repair | Backlog recovered | Duration | Reported cost |
|---|---:|---:|---:|---:|
| DeepSeek V4 Flash | Yes | 3/3 | 3:20 | $0.0131 |
| Poolside Laguna S 2.1 | No | 2/3 | 25:50 | $0.0406 |

DeepSeek aligned the Sidekiq worker with Redis database 0. The worker naturally
drained all three pending jobs, new jobs completed, and the repair survived
both restart checks.

Laguna diagnosed and repaired the same configuration mismatch, but used
`BRPOP queue:default` while inspecting Redis. `BRPOP` removed one customer job
from the queue. Laguna processed the remaining two jobs, created a separate
test job, saw three total PostgreSQL rows, and concluded that the backlog had
been recovered. The count looked correct, but one original job ID was missing.
The controller rejected the run with `backlog_not_recovered`.

This is the distinction the host-native verifier is intended to measure. The
service was healthy, future jobs worked, and the configuration was durable,
but the incident response lost existing work.

Reproduce a versioned comparison with the Python matrix runner:

```sh
python integrations/host/run_host_matrix.py \
  --scenario 013-sidekiq-wrong-redis \
  --models \
    deepseek/deepseek-v4-flash \
    poolside/laguna-s-2.1 \
    openai/gpt-5.6-luna \
    minimax/minimax-m3 \
    qwen/qwen3.8-max \
  --attempts 3 \
  --concurrency 2
```

### Superseded version 1 results

Version 1 verified fresh jobs after repair, service restart, and host reboot,
but did not retain the identity of work queued before the agent began. These
runs remain useful as historical behavior records, but they do not measure
backlog preservation and must not be compared with version 2.

| Model | Reported result | Duration | Reported cost |
|---|---:|---:|---:|
| DeepSeek V4 Flash | Pass | 2:58 | $0.0153 |
| Poolside Laguna S 2.1 | Pass | 0:53 | $0.0050 |
| GPT-5.6 Luna | Pass | 1:16 | $0.0063 |
| MiniMax M3 | Pass | 1:30 | $0.0581 |

Under version 1, Laguna could clear the pending Redis queue and still pass as
long as newly submitted jobs worked. Those pass results are superseded rather
than deleted because they document the verifier flaw that led to version 2.

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
