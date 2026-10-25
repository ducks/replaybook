# Replaybook benchmarks

Replaybook evaluates whether an agent can diagnose and durably repair a running
system. A passing health check is not enough. The verifier restarts the affected
services and checks the user-facing behavior again.

These results are evaluation history, not a definitive model ranking. Runs made
with different scenario sets, verifier versions, agent harnesses, or attempt
counts should not be compared as if they were one controlled experiment.

<!-- replaybook:current-benchmark:start -->
## Frozen Core: GLM Flash, Step 5 Preview, and Nemotron Ultra

Three high-reasoning configurations attempted eleven Core incidents three times each: 85 durable repairs in 99 evaluated trials, with $5.9719 in provider-reported API cost.

Benchmark release: `20261025.0.1`
Benchmark tier: `core`

Scenario packs: `ducks/replaybook-infra@20261024.1.1`

| Model | Durable repairs | Pass rate | Median | Known cost | Cost per repair |
|---|---:|---:|---:|---:|---:|
| GLM 5.3 Flash (high) · openrouter | 31/33 | 94% | 1:12 | $0.4194 | $0.0135 |
| Step 5 Preview (high) · openrouter | 30/33 | 91% | 1:24 | $2.1953 | $0.0732 |
| Nemotron 3 Ultra 550B A55B (high) · openrouter | 24/33 | 73% | 1:28 | $3.3572 | $0.1399 |
| **Total** | **85/99** | **86%** | **1:24** | **$5.9719** | **$0.0703** |

### Execution recording

Medians across trials with execution recording. Round time sums recorded model/agent steps, not pure inference. It can include tools; parallel tool durations can overlap. Do not add these columns. First non-read means a potentially mutating tool, not a confirmed write; after non-read is the remaining agent time.

| Model | Recorded | Rounds | Round time | Tools | Tool time | First non-read | After non-read |
|---|---:|---:|---:|---:|---:|---:|---:|
| GLM 5.3 Flash (high) · openrouter | 33/33 | 15 | 0:44 | 20 | 0:12 | 0:02 | 1:10 |
| Step 5 Preview (high) · openrouter | 33/33 | 19 | 0:59 | 26 | 0:17 | 0:01 | 1:21 |
| Nemotron 3 Ultra 550B A55B (high) · openrouter | 33/33 | 34 | 0:57 | 39 | 0:14 | 0:02 | 1:24 |

GLM 5.3 Flash passed 31/33 (93.9%), with a 72-second median agent duration and $0.4194 in provider-reported API cost. Reported cost per durable repair was $0.0135 for this run.

Step 5 Preview passed 30/33 (90.9%), with a 84-second median agent duration and $2.1953 in provider-reported API cost. Reported cost per durable repair was $0.0732 for this run.

Nemotron 3 Ultra 550B A55B passed 24/33 (72.7%), with a 88-second median agent duration and $3.3572 in provider-reported API cost. Reported cost per durable repair was $0.1399 for this run.

GLM Flash failed two trials: the auth-secret rotation trial passed immediate and restart checks but completed only 1/3 concurrent session requests after reboot. The Rails pool trial passed immediate and restart checks, but the VM did not shut down for the controller-requested reboot; the retained console does not establish the cause. It remains an evaluated failure, with no infrastructure exemption or replacement trial.

Step failed three trials: Rails pool recovery completed 4/8 concurrent checkouts after restart; interrupted deployment left the green instance on the previous release; TLS recovery accepted 0/4 fresh deliveries after service restart despite passing immediate verification.

Nemotron failed nine trials: all three interrupted-deployment attempts left the green instance on the previous release; two shared-upload attempts did not recover controller-owned accepted uploads; one Rust attempt completed 0/100 concurrent resource checks; one auth rollout left the old instance on its previous release; one disk-pressure attempt completed only 5/6 receipt writes after restart; and one TLS attempt returned an unverified transport-security state.

The legacy top-level results omit failure categories for the two failed TLS trials. Their retained controller phase records identify new_deliveries_not_accepted for Step and tls_peer_validation_disabled for Nemotron. These observations explain the evidence; the original normalized categories and outcomes are unchanged.

Partial concurrent assertions retain successful request counts, not complete error details for every unsuccessful response. The evidence establishes failed verification phases, but does not establish all underlying causes. Three attempts per scenario are too few to treat these point estimates as definitive rankings.

### Scenario breakdown

| Scenario | Version | GLM 5.3 Flash (high) · openrouter | Step 5 Preview (high) · openrouter | Nemotron 3 Ultra 550B A55B (high) · openrouter |
|---|---:|---:|---:|---:|
| 001-nginx-502-host | v1 | 3/3, 0:27 | 3/3, 0:52 | 3/3, 0:31 |
| 013-sidekiq-wrong-redis | v3 | 3/3, 0:37 | 3/3, 1:20 | 3/3, 0:51 |
| 015-sidekiq-poison-pill | v1 | 3/3, 2:04 | 3/3, 1:59 | 3/3, 2:01 |
| 016-rails-pool-exhaustion | v1 | 2/3, 1:47 | 2/3, 1:28 | 3/3, 1:31 |
| 017-partial-rails-rollout | v1 | 3/3, 1:42 | 3/3, 2:12 | 3/3, 1:33 |
| 019-rust-fd-leak | v1 | 3/3, 0:43 | 3/3, 1:07 | 2/3, 1:21 |
| 021-discourse-shared-uploads | v1 | 3/3, 0:56 | 3/3, 2:38 | 1/3, 2:30 |
| 023-auth-secret-rollout | v1 | 2/3, 0:31 | 3/3, 1:11 | 2/3, 0:54 |
| 024-discourse-interrupted-deploy | v1 | 3/3, 2:41 | 2/3, 1:27 | 0/3, 3:50 |
| 028-nix-store-disk-pressure | v1 | 3/3, 2:09 | 3/3, 1:14 | 2/3, 1:27 |
| 033-tls-trust-rotation | v2 | 3/3, 1:17 | 2/3, 1:26 | 2/3, 3:15 |

### Failure categories

- `accepted_uploads_not_recovered`: 2
- `current_sessions_unavailable`: 1
- `database_pool_exhausted`: 1
- `host_reboot_failed`: 1
- `new_receipts_not_writable`: 1
- `release_not_converged`: 4
- `resource_growth_unbounded`: 1
- `rollout_incomplete`: 1

### Source matrices

- `matrix`: stepfun/step-5-preview, nvidia/nemotron-3-ultra-550b-a55b, z-ai/glm-5.3-flash; Replaybook `37506bdd`; reasoning high

### Run notes

- Core 2026.10.24.2: eleven scenarios, three attempts each, high reasoning, and a 900-second agent deadline. All 99 final trials were evaluated, none are missing or unavailable, and all 99 report cost. All 85 successful repairs passed immediate, service-restart, and host-reboot verification.
- This release uses the same frozen controller, scenario fingerprints, manifest, shared OpenRouter transport, and Claux executable as releases 20261024.0.0 and 20261025.0.0. Frozen cohort identity: 424667b247abacec1af6e962a3115aaa89ce7badbea3bd264b80dd32f6b25b76. Claux executable SHA-256: b959ef677ff7b75dea88b1915cde894eb7dc223e0cb985fbb6c61858d2e24e41. This is a custom build, not an official Claux release.
- The selected Nemotron endpoint is the paid nvidia/nemotron-3-ultra-550b-a55b configuration, not its free endpoint. Provider routing, model identities, high reasoning, and reported usage remain visible for each configuration.
- The session initially recorded 97 results. Operator inspection observed a VM SSH port-forward bind failure in a missing Step poison-pill trial and an incident preflight failure in a missing GLM TLS trial; neither reached agent evaluation. Normal resume reran only those two missing trials against the original execution snapshot, and both passed. All 97 recorded outcomes were preserved; no evaluated failures were retried.
- Audit limitation: normal resume replaced the missing trials' original logs and summary entries rather than archiving them. The pre-resume causes above are operator observations, not separately retained raw startup artifacts. Final result files, immutable execution metadata, and the resumed trials' final evidence are retained. No source evidence has been reconstructed or fabricated.
- All 14 failed evaluated trials remain in the denominator, including the GLM VM shutdown/reboot failure. No provider, harness, or infrastructure exemptions have been applied and no recorded outcome has been corrected.
- Scores are small-sample point estimates for the selected configuration and frozen benchmark contract. Compatible releases may be displayed together, but attempts are never pooled, best-selected, or rewritten to make comparisons complete.
- Public evidence includes normalized trial results, reported usage, immutable execution metadata, and source digests. Private transcripts, credentials, full VM logs, and local absolute paths are excluded.

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
