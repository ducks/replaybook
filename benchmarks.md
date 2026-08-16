# Replaybook benchmarks

Replaybook evaluates whether an agent can diagnose and durably repair a running
system. A passing health check is not enough. The verifier restarts the affected
services and checks the user-facing behavior again.

These results are evaluation history, not a definitive model ranking. Runs made
with different scenario sets, verifier versions, agent harnesses, or attempt
counts should not be compared as if they were one controlled experiment.

<!-- replaybook:current-benchmark:start -->
## Five agents across 13 infrastructure incidents

Five infrastructure agents each attempted 13 host-native incidents once under the same frozen harness and scenario pack. The suite spans Nginx, Ruby, Rails, Sidekiq, Node, Rust, Python, deployment, authentication, and Discourse-shaped failures, with every repair verified immediately, after service restart, and after host reboot.

Benchmark release: `20260815.0.1`

Scenario packs: `ducks/replaybook-infra@20260814.0.0`

| Model | Durable repairs | Pass rate | Median | Known cost | Cost per repair |
|---|---:|---:|---:|---:|---:|
| GLM 5.2 (high) | 13/13 | 100% | 2:31 | $0.5196 | $0.0400 |
| DeepSeek V4 Flash 0731 (high) | 12/13 | 92% | 4:33 | $0.1029 | $0.0086 |
| GPT-5.6 Luna (high) | 12/13 | 92% | 1:56 | $0.1929+ | $0.0161+ |
| Claude Sonnet 5 (high) | 12/13 | 92% | 2:03 | $10.8662 | $0.9055 |
| Gemini 3.7 Flash (high) | 10/13 | 77% | 3:09 | $1.7917 | $0.1792 |
| **Total** | **59/65** | **91%** | **2:31** | **$13.4734+** | **$0.2284+** |

### Execution recording

Medians across trials with transcript schema v2 recording. First non-read is time before the first potentially mutating tool call; after non-read is the remaining agent time. Model and tool time can overlap.

| Model | Recorded | Rounds | Model time | Tools | Tool time | First non-read | After non-read |
|---|---:|---:|---:|---:|---:|---:|---:|
| GLM 5.2 (high) | 13/13 | 16 | 2:04 | 25 | 0:10 | 0:03 | 2:26 |
| DeepSeek V4 Flash 0731 (high) | 13/13 | 17 | 3:52 | 23 | 0:13 | 0:07 | 4:26 |
| GPT-5.6 Luna (high) | 12/13 | 17 | 1:20 | 34 | 0:13 | 0:06 | 1:42 |
| Claude Sonnet 5 (high) | 13/13 | 17 | 1:58 | 22 | 0:11 | 0:05 | 1:58 |
| Gemini 3.7 Flash (high) | 13/13 | 40 | 2:36 | 39 | 0:05 | 0:05 | 3:03 |

GLM 5.2 made 13 durable repairs in 13 attempts with a 2:31 median. It was the only model to sweep the suite and spent $0.5196, about $0.0400 per durable repair.

DeepSeek V4 Flash 0731 made 12 durable repairs in 13 attempts and spent $0.1029, about $0.0086 per repair. It was the least expensive repair agent in this cohort, though its 4:33 median was the slowest among the four non-Sonnet fleet models.

GPT-5.6 Luna made 12 durable repairs in 13 attempts with the fastest median at 1:56. Its known spend was $0.1929, at least $0.0161 per durable repair; the missing timeout usage means the true value is higher.

Claude Sonnet 5 made 12 durable repairs in 13 attempts with a 2:03 median. It spent $10.8662, about $0.9055 per durable repair, making its observed repairs roughly 105 times as expensive as DeepSeek's in this cohort.

Gemini 3.7 Flash made 10 durable repairs in 13 attempts with a 3:09 median and spent $1.7917, about $0.1792 per durable repair. Its three failures were later provider rejections after meaningful inference: one corrupted thought signature and two content-policy responses. Replaybook counts them as evaluated failures while preserving that distinction from verifier failures.

The remaining failures separated cleanly: DeepSeek did not converge the interrupted deployment, Luna timed out with exports still failing, and Sonnet did not converge the interrupted deployment.

### Scenario breakdown

| Scenario | Version | GLM 5.2 (high) | DeepSeek V4 Flash 0731 (high) | GPT-5.6 Luna (high) | Claude Sonnet 5 (high) | Gemini 3.7 Flash (high) |
|---|---:|---:|---:|---:|---:|---:|
| 001-nginx-502-host | v1 | 1/1, 0:57 | 1/1, 1:57 | 1/1, 3:05 | 1/1, 1:04 | 1/1, 1:34 |
| 013-sidekiq-wrong-redis | v2 | 1/1, 1:12 | 1/1, 5:39 | 1/1, 1:24 | 1/1, 1:51 | 1/1, 2:02 |
| 014-missing-rails-migration | v2 | 1/1, 2:38 | 1/1, 5:01 | 1/1, 2:06 | 1/1, 1:52 | 1/1, 3:40 |
| 015-sidekiq-poison-pill | v1 | 1/1, 3:44 | 1/1, 5:14 | 1/1, 3:29 | 1/1, 4:01 | 0/1, 1:00 |
| 016-rails-pool-exhaustion | v1 | 1/1, 1:26 | 1/1, 2:49 | 1/1, 1:23 | 1/1, 1:51 | 1/1, 3:17 |
| 017-partial-rails-rollout | v1 | 1/1, 2:31 | 1/1, 4:33 | 1/1, 1:45 | 1/1, 3:53 | 0/1, 1:25 |
| 018-node-event-loop-blocking | v1 | 1/1, 2:52 | 1/1, 3:48 | 1/1, 2:16 | 1/1, 2:03 | 1/1, 3:09 |
| 019-rust-fd-leak | v1 | 1/1, 2:14 | 1/1, 4:08 | 1/1, 1:33 | 1/1, 2:14 | 1/1, 1:59 |
| 020-python-gunicorn-saturation | v1 | 1/1, 4:20 | 1/1, 10:37 | 0/1, 15:31 | 1/1, 2:09 | 1/1, 4:12 |
| 021-discourse-shared-uploads | v1 | 1/1, 2:42 | 1/1, 11:19 | 1/1, 2:25 | 1/1, 4:13 | 1/1, 3:56 |
| 022-discourse-multisite-migration | v1 | 1/1, 1:19 | 1/1, 3:46 | 1/1, 1:08 | 1/1, 1:51 | 0/1, 1:39 |
| 023-auth-secret-rollout | v1 | 1/1, 2:49 | 1/1, 3:17 | 1/1, 1:31 | 1/1, 2:03 | 1/1, 4:39 |
| 024-discourse-interrupted-deploy | v1 | 1/1, 1:57 | 0/1, 5:04 | 1/1, 1:56 | 0/1, 5:19 | 1/1, 3:15 |

### Failure categories

- `agent_runtime_error`: 3
- `agent_timeout`: 1
- `release_not_converged`: 2

### Source matrices

- `host-matrix-2026-08-15__21-56-08.20e83f`: deepseek/deepseek-v4-flash-0731, google/gemini-3.7-flash, openai/gpt-5.6-luna, z-ai/glm-5.2; Replaybook `7f4b117a`; reasoning high
- `host-matrix-2026-08-15__21-07-50.e717e8`: anthropic/claude-sonnet-5; Replaybook `7f4b117a`; reasoning high
- `host-matrix-2026-08-15__21-28-32.898d2a`: anthropic/claude-sonnet-5; Replaybook `7f4b117a`; reasoning high
- `host-matrix-2026-08-15__20-18-41.a9a4e6`: anthropic/claude-sonnet-5; Replaybook `7f4b117a`; reasoning high

### Run notes

- All 65 trials were evaluated using Replaybook commit 7f4b117, scenario pack ducks/replaybook-infra 20260814.0.0, Claux v20260815.0.0, high reasoning, and a 900-second agent timeout. No unavailable trials were excluded.
- Each model attempted each scenario once. This broad smoke cohort shows where models separated, but it is not enough repetition to establish a universal reliability ranking.
- GPT-5.6 Luna reported usage for 12 of 13 trials, so its known cost and cost per durable repair are lower bounds. The timed-out Python saturation trial reported no usage.

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
