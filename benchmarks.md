# Replaybook benchmarks

Replaybook evaluates whether an agent can diagnose and durably repair a running
system. A passing health check is not enough. The verifier restarts the affected
services and checks the user-facing behavior again.

These results are evaluation history, not a definitive model ranking. Runs made
with different scenario sets, verifier versions, agent harnesses, or attempt
counts should not be compared as if they were one controlled experiment.

<!-- replaybook:current-benchmark:start -->
## Frozen Core: MiniMax, Mistral Large 4, and Reflection Beam

Three high-reasoning configurations attempted eleven Core incidents three times each: 94 scored durable repairs in 99 evaluated trials. All five failures were MiniMax agent timeouts; two passed post-timeout durability checks but remain deadline failures. Recorded API spend is at least $11.2293.

Benchmark release: `20261027.0.0`
Benchmark tier: `core`

Scenario packs: `ducks/replaybook-infra@20261024.1.1`

| Model | Durable repairs | Pass rate | Median | Known cost | Cost per repair |
|---|---:|---:|---:|---:|---:|
| MiniMax M3.1 Flash (high) · openrouter | 28/33 | 85% | 9:00 | $1.4007+ | $0.0500+ |
| Mistral Large 4 (high) · openrouter | 33/33 | 100% | 3:00 | $8.6620 | $0.2625 |
| Reflection Beam 1.0 501B A23B (high) · openrouter | 33/33 | 100% | 1:03 | $1.1666 | $0.0354 |
| **Total** | **94/99** | **95%** | **3:03** | **$11.2293+** | **$0.1195+** |

### Post-timeout verification

2 repairs became durable after the agent deadline. They remain scored as `agent_timeout`; post-timeout verification records the later operational outcome separately.

### Execution recording

Medians across trials with execution recording. Round time sums recorded model/agent steps, not pure inference. It can include tools; parallel tool durations can overlap. Do not add these columns. First non-read means a potentially mutating tool, not a confirmed write; after non-read is the remaining agent time.

| Model | Recorded | Rounds | Round time | Tools | Tool time | First non-read | After non-read |
|---|---:|---:|---:|---:|---:|---:|---:|
| MiniMax M3.1 Flash (high) · openrouter | 32/33 | 31.5 | 7:55 | 44.5 | 0:26 | 0:02 | 8:42 |
| Mistral Large 4 (high) · openrouter | 33/33 | 26 | 2:24 | 36 | 0:14 | 0:02 | 2:56 |
| Reflection Beam 1.0 501B A23B (high) · openrouter | 33/33 | 25 | 0:44 | 27 | 0:15 | 0:01 | 0:59 |

MiniMax M3.1 Flash passed 28/33 (84.8%), with a 540-second median agent duration and at least $1.4007 in provider-reported cost. Cost reporting covers 32/33 trials; recorded cost per successful repair was at least $0.0500.

Mistral Large 4 passed 33/33 (100.0%), with a 180-second median agent duration and $8.6620 in provider-reported cost. Cost reporting covers 33/33 trials; recorded cost per successful repair was $0.2625.

Reflection Beam 1.0 501B A23B passed 33/33 (100.0%), with a 63-second median agent duration and $1.1666 in provider-reported cost. Cost reporting covers 33/33 trials; recorded cost per successful repair was $0.0354.

All five MiniMax failures exceeded the 900-second agent deadline. In the partial Rails rollout and Rust file-descriptor trials, post-timeout verification confirmed durable repairs through immediate, service-restart, and host-reboot checks. Both remain deadline failures: passing verification after the agent was stopped does not change whether it completed within the deadline. These two diagnostic durable_repairs_after_timeout outcomes are retained separately, not promoted to scored passes.

The shared-uploads timeout did not recover a historical avatar: post-timeout immediate verification returned HTTP 404 for the historical-upload check. The auth-rollout timeout left the old instance on its previous release. The disk-pressure timeout passed its immediate post-timeout check but service restart failed; there is no confirmed durable repair in those three trials.

The four timeout trials with checkpointed records contain 30, 29, 19, and 32 model rounds, respectively, and 39, 40, 31, and 40 tool calls. Those checkpoints are meaningful execution evidence but may omit the final in-flight request. The disk-pressure timeout has no final usage or execution-recording data, and its cost is unknown rather than zero.

Mistral and Reflection Beam each passed all 33 trials, with different observed durations and provider-reported costs. This small sample does not establish a universal capability ranking or prove either configuration will always pass these incidents.

All comparisons retain the exact release, provider, model, reasoning, harness, and benchmark identity. Timing reflects the whole agent run, not pure inference, and can include provider and host variability.

### Scenario breakdown

| Scenario | Version | MiniMax M3.1 Flash (high) · openrouter | Mistral Large 4 (high) · openrouter | Reflection Beam 1.0 501B A23B (high) · openrouter |
|---|---:|---:|---:|---:|
| 001-nginx-502-host | v1 | 3/3, 7:39 | 3/3, 3:16 | 3/3, 2:45 |
| 013-sidekiq-wrong-redis | v3 | 3/3, 9:00 | 3/3, 3:22 | 3/3, 1:01 |
| 015-sidekiq-poison-pill | v1 | 3/3, 11:37 | 3/3, 7:34 | 3/3, 1:19 |
| 016-rails-pool-exhaustion | v1 | 3/3, 8:16 | 3/3, 2:54 | 3/3, 2:47 |
| 017-partial-rails-rollout | v1 | 2/3, 11:18 | 3/3, 2:26 | 3/3, 1:53 |
| 019-rust-fd-leak | v1 | 2/3, 9:23 | 3/3, 1:47 | 3/3, 1:00 |
| 021-discourse-shared-uploads | v1 | 2/3, 14:56 | 3/3, 11:13 | 3/3, 5:56 |
| 023-auth-secret-rollout | v1 | 2/3, 6:59 | 3/3, 2:14 | 3/3, 1:02 |
| 024-discourse-interrupted-deploy | v1 | 3/3, 10:19 | 3/3, 3:00 | 3/3, 0:54 |
| 028-nix-store-disk-pressure | v1 | 2/3, 8:05 | 3/3, 3:15 | 3/3, 1:03 |
| 033-tls-trust-rotation | v2 | 3/3, 1:25 | 3/3, 2:29 | 3/3, 1:01 |

### Failure categories

- `agent_timeout`: 5

### Source matrices

- `matrix`: minimax/minimax-m3.1-flash, mistralai/mistral-large-4-0, reflection/beam-1.0-501b-a23b; Replaybook `37506bdd`; reasoning high

### Run notes

- Core 2026.10.24.2: eleven scenarios, three attempts each, high reasoning, and a 900-second agent deadline. All 99 trials were evaluated; none are missing or unavailable. No recovery runs or evaluated-failure retries were needed.
- All 94 scored passes passed immediate, service-restart, and host-reboot verification. All five deadline failures remain in the denominator with their original outcomes, including two systems that passed the controller's post-timeout durability checks. No scoring exemptions or post-run corrections have been applied.
- This release uses the same frozen controller, scenario fingerprints, manifest, shared OpenRouter protocol, and Claux executable as expanded Core releases beginning with 20261024.0.0. Frozen cohort identity: 424667b247abacec1af6e962a3115aaa89ce7badbea3bd264b80dd32f6b25b76. Claux executable SHA-256: b959ef677ff7b75dea88b1915cde894eb7dc223e0cb985fbb6c61858d2e24e41. This is a custom build, not an official Claux release.
- This batch used one concurrent VM for memory headroom. Some earlier compatible releases used two workers. Worker concurrency and host/provider conditions can affect timing; they do not change scenario weighting, the per-agent deadline, or the verification requirements.
- Provider-reported cost is present for 98/99 trials: MiniMax 32/33, Mistral 33/33, and Beam 33/33. The MiniMax disk-pressure timeout has no cost record. Other timed-out MiniMax trials contain partial checkpointed usage; MiniMax and aggregate reported spend are lower bounds, not complete billed totals.
- The exact IDs are minimax/minimax-m3.1-flash, mistralai/mistral-large-4-0, and reflection/beam-1.0-501b-a23b through OpenRouter. Earlier exploratory MiniMax and Mistral evidence used different benchmark conditions and is not pooled into this release.
- Three attempts per scenario are a small sample. Compatible releases may be displayed together, but model headline scores never pool attempts across releases or select best runs. Scenario failure rates are descriptive configuration samples, not intrinsic difficulty or score weights.
- Public evidence includes normalized trial results, verification flags and post-timeout diagnostics, provider-reported usage, immutable execution metadata, and source digests. Private transcripts, credentials, full VM logs, and local absolute paths are excluded.

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
