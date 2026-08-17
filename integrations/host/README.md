# Host-native evaluation

This integration evaluates an infrastructure agent on a real disposable Linux
host. The agent does not receive a Docker socket and does not manage sibling
containers.

The local controller builds and boots a scenario-selected NixOS VM. Five
scenarios are currently available:

- `001-nginx-502-host`: Nginx points at the wrong backend port.
- `013-sidekiq-wrong-redis`: a healthy Ruby web service enqueues checkout
  confirmation jobs into Redis database 0 while Sidekiq watches database 1.
  Successful jobs write a durable completion record to PostgreSQL. Its verifier
  requires both new jobs and the pre-existing backlog to complete, so deleting
  or abandoning queued work does not pass.
- `014-missing-rails-migration`: a deployed Ruby worker expects a PostgreSQL
  column from a migration that was shipped but never applied. The verifier
  requires the migration record, the schema change, retry recovery for the
  exact pre-existing jobs, and one execution of every new job.
- `015-sidekiq-poison-pill`: one poison payload blocks Sidekiq's only worker
  thread. The verifier requires the poison job to be quarantined, the valid
  backlog to be recovered, and future poison work to stop blocking valid jobs.
- `016-rails-pool-exhaustion`: four Puma threads share an undersized
  ActiveRecord pool. The verifier requires failed checkouts to be recovered
  and concurrent traffic to succeed after restart and reboot.

Each scenario supplies its NixOS topology, incident instruction, reference
repair, broken-state preflight, and external verifier. The controller verifies
the repair through the user-facing HTTP boundary. Model VMs never receive the
reference repair or verifier. The runner only copies the reference repair into
the VM for an explicit `--oracle` run and asserts that it is absent before
starting an agent adapter.

## Build a scenario with the skill

Replaybook ships a Codex skill for turning an operational failure into a
host-native scenario with a durable verifier, oracle repair, controller-owned
state, failure categories, and leak checks. Install it from a clone:

```sh
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/replaybook-build-scenario" \
  ~/.codex/skills/replaybook-build-scenario
```

Then ask Codex:

```text
Use $replaybook-build-scenario to build a scenario for <incident>.
```

The skill includes declarative scenario templates and a static validator. It
requires an oracle run before any paid model matrix and treats restart and
reboot survival, backlog preservation, and benchmark leak review as part of
scenario correctness.

## Declarative scenario lifecycle

New host scenarios can define their lifecycle in `scenario.toml`. The typed
manifest contains scenario version and topology metadata plus ordered
`preflight` and `verify` steps. The generic phase runner currently supports:

- `wait_http`: poll an HTTP assertion with bounded request and phase timeouts.
- `concurrent_http`: generate controller-owned IDs, issue bounded concurrent
  requests, assert a success range, and persist all or failed IDs.
- `replay_http`: retry exact IDs from controller state and require recovery.

Each assertion can name its own `failure_category`. On failure, the phase
runner writes structured `phase-failure.json`; adding a scenario category no
longer requires assigning another shell exit code in the host runner.

All bundled host-native scenarios are fully declarative. Their preflight and
verifier behavior is represented entirely by manifest steps. Legacy
`scenario.conf`, `preflight.sh`, and `verify.sh` hooks remain supported for
external scenarios that have not migrated yet.

### Guest image leak audit

Declarative scenarios can reject answer-shaped text that accidentally reaches
the built VM:

```toml
[guest_leak_audit]
forbidden_strings = ["partial rollout", "intentionally wrong redis database"]
scan_paths = ["/etc/replaybook", "/var/lib/checkout"]
```

After the services become ready and before preflight or agent staging, the
controller scans systemd metadata, guest filesystem names and symlink targets,
Nix store entry names, and file contents beneath `scan_paths`. Forbidden
strings remain controller-side. A match aborts the trial without revealing the
matched text to the guest or recording it in the result. Real operational
evidence, such as a connection-refused log containing the configured port,
should not be forbidden. Labels that explain the diagnosis should be.

## External scenario packs

Host incidents can live outside the Replaybook repository. A scenario pack is
a directory containing `replaybook-pack.toml` and one directory per scenario:

```text
company-incidents/
  replaybook-pack.toml
  database-failover/
    scenario.toml
    nixos.nix
    instruction.md
    oracle.sh
```

The pack manifest gives the source a stable identity and independently
versioned release:

```toml
[pack]
id = "example/company-incidents"
version = "20260809.0.0"
```

Select it for a single run or a matrix with `--scenario-pack`:

```sh
integrations/host/run-host-native.sh \
  --scenario-pack ../company-incidents \
  --scenario database-failover \
  --oracle

python integrations/host/run_host_matrix.py \
  --scenario-pack ../company-incidents \
  --scenario database-failover \
  --models deepseek/deepseek-v4-flash-0731 \
  --attempts 3
```

Repeat `--scenario-pack` to combine packs with distinct scenario IDs. Supplying
any pack replaces the bundled default for that command. Replaybook rejects
duplicate pack or scenario IDs and records the selected pack ID and version in
the matrix metadata. The benchmark publisher refuses to combine results from
different pack revisions.

The selected harness runs directly as root on that VM and investigates with
normal Linux tools such as `systemctl`, `journalctl`, `ps`, `ss`, and the
filesystem. The controller remains outside the incident host and verifies the
HTTP endpoint after the repair, after restarting both services, and after
rebooting the entire VM. The VM itself is the disposable security boundary.

The bundled Claux adapter does not receive the real OpenRouter credential.
Replaybook keeps that key in a host-side forwarding proxy and exposes only a
localhost endpoint plus a non-secret placeholder token inside the VM. The
proxy replaces the placeholder authorization header before forwarding each
request. This prevents an agent running as root from recovering the credential
from files, child-process environments, or `/proc`. Raw provider credentials
never enter retained execution snapshots or transcripts.

## Reference smoke test

Run the reference repair without model credentials:

```sh
integrations/host/run-host-native.sh --oracle
```

Run the Ruby and Sidekiq reference repair:

```sh
integrations/host/run-host-native.sh \
  --scenario 013-sidekiq-wrong-redis \
  --oracle
```

Run the missing migration reference repair:

```sh
integrations/host/run-host-native.sh \
  --scenario 014-missing-rails-migration \
  --oracle
```

## Run Claux

With `OPENROUTER_API_KEY` set:

```sh
integrations/host/run-host-native.sh \
  --scenario 013-sidekiq-wrong-redis \
  --model deepseek/deepseek-v4-flash
```

Use `--ssh-port` and `--http-port` when running workers concurrently. Set
`REPLAYBOOK_HOST_CLAUX_BINARY` to bake a local binary into the guest instead of
using the cached default release. Replaybook downloads each pinned Claux release
once on the host and includes it in the immutable guest closure. Claux receives
900 seconds by default;
override it with `--agent-timeout-seconds`.

Cold VM builds get 300 seconds to expose SSH. The credential proxy and reverse
tunnel each get 30 seconds. The SSH command that requests a host reboot gets 15
seconds before Replaybook proceeds to its bounded shutdown and readiness
polls. Override those host-side windows with `REPLAYBOOK_HOST_VM_READY_TIMEOUT`,
`REPLAYBOOK_HOST_REBOOT_COMMAND_TIMEOUT`, and
`REPLAYBOOK_HOST_PROXY_READY_TIMEOUT`.

Interrupted matrices normally resume through their frozen controller. When a
controller-only infrastructure fix must apply to pending trials, add
`--refresh-controller`. Replaybook still uses the frozen scenario packs and
reports every harness version present in the combined results.

## Run another harness

Replaybook owns the incident VM, instruction, lifecycle, and verification. An
agent adapter owns only the translation between Replaybook's contract and a
particular harness CLI. Supply an executable adapter and, when useful, a
harness binary or other artifact:

```sh
integrations/host/run-host-native.sh \
  --scenario 013-sidekiq-wrong-redis \
  --model vendor/model \
  --agent-adapter ./run-my-agent.sh \
  --agent-payload ./my-agent \
  --agent-env-file ./my-agent.env \
  --agent-name my-agent
```

The adapter runs as root with `/root` as its working directory. Replaybook
exports:

- `REPLAYBOOK_INSTRUCTION_FILE`: incident prompt written by the scenario.
- `REPLAYBOOK_MODEL`: the scheduled model identifier.
- `REPLAYBOOK_WORKSPACE`: the host workspace, currently `/root`.
- `REPLAYBOOK_RESULT_FILE`: required normalized result path.
- `REPLAYBOOK_TRANSCRIPT_FILE`: optional transcript path.
- `REPLAYBOOK_AGENT_PAYLOAD`: optional staged payload path.
- `REPLAYBOOK_EVAL_ROOT`: private evaluation directory inside the VM.

The adapter must write a JSON object to `REPLAYBOOK_RESULT_FILE` containing
`schema_version: 1`, the configured harness name, and the scheduled `model`.
It may also report `result`, `outcome`, and `usage`; Replaybook copies `usage`
into the verified trial result and aggregates token and cost fields when they
are available. A transcript is optional and remains harness-defined JSON.

The environment file is copied with mode 0600, sourced, and unlinked before the
adapter starts. It should contain shell assignments required by that harness.
The resulting values remain in the adapter process environment, so custom
adapters are responsible for preventing their tools from exposing secrets.
Replaybook does not require an OpenRouter key for custom adapters. The bundled
Claux adapter instead uses the host-side credential proxy and remains the
default when `--agent-adapter` is omitted.

Host boot, reboot, and service-readiness checks use wall-clock deadlines, so
repeated SSH connection attempts cannot extend a failed trial indefinitely.
When Claux supports graceful one-shot signal cancellation, an agent timeout
also preserves its partial messages, tool trace, token usage, and known cost.

## Run a model matrix

Replaybook also ships a
[`replaybook-run-benchmark`](../../skills/replaybook-run-benchmark/SKILL.md)
skill for planning a matrix, checking host capacity, generating Bash or
Nushell-safe commands, resuming interrupted runs, interpreting failures, and
publishing compatible results. Install it from a clone:

```sh
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/replaybook-run-benchmark" \
  ~/.codex/skills/replaybook-run-benchmark
```

Then ask Codex:

```text
Use $replaybook-run-benchmark to run and interpret this benchmark.
```

The Python matrix runner schedules multiple models and attempts while the Bash
runner remains the single-worker primitive:

```sh
python integrations/host/run_host_matrix.py \
  --scenario 013-sidekiq-wrong-redis \
  --models \
    deepseek/deepseek-v4-flash \
    poolside/laguna-s-2.1 \
    openai/gpt-5.6-luna \
    minimax/minimax-m3 \
  --reasoning-efforts low high \
  --attempts 3 \
  --concurrency 2
```

The matrix runner accepts the same `--agent-adapter`, `--agent-payload`,
`--agent-env-file`, and `--agent-name` options. One matrix evaluates one
harness across any number of scenarios, models, reasoning efforts, and attempts.
Reasoning effort is supported by the bundled Claux adapter and is recorded in
run IDs, individual results, summary tables, and benchmark metadata. Custom
adapters remain responsible for defining their own configuration dimensions.
Worker logs show
both launch position and completed progress, such as `starting 3 of 15` and
`completed 1 of 15`, so long concurrent matrices remain easy to track.

If a matrix is interrupted, resume it in place:

```sh
python integrations/host/run_host_matrix.py \
  --resume jobs/host-matrix-2026-08-11__18-47-25.10fa16 \
  --concurrency 2
```

Resume reconstructs the complete job plan from `benchmark.json`, verifies the
saved harness and scenario-pack hashes, keeps every identity-matching
`result.json`, removes partial output only for unfinished jobs, and schedules
the remainder on their original port assignments. The final summary covers the
whole matrix, not only the resumed workers. A custom adapter whose original
matrix used `--agent-env-file` must supply that file again; Replaybook verifies
its hash without retaining the secret-bearing file.

## Run an executable benchmark manifest

A benchmark repository can freeze its scenario pack, scenario versions,
attempt count, timeout, verification policy, and required host harness in a
`benchmark.toml`. Run that controlled experiment without repeating its matrix
dimensions on the command line:

```sh
python integrations/host/run_host_matrix.py \
  --benchmark ../replaybook-infra/benchmark.toml \
  --models deepseek/deepseek-v4-flash-0731 \
  --concurrency 2
```

The submitting user still chooses the model, reasoning effort, adapter,
credentials, concurrency, and output directory. Replaybook rejects
`--scenario`, `--scenario-pack`, `--attempts`, or `--agent-timeout-seconds`
overrides when `--benchmark` is present.

Validate the manifest, pack identity, scenario versions, and required harness
without starting a VM:

```sh
python integrations/host/run_host_matrix.py \
  --benchmark ../replaybook-infra/benchmark.toml \
  --check
```

Run each benchmark scenario once with its controller-only reference repair:

```sh
python integrations/host/run_host_matrix.py \
  --benchmark ../replaybook-infra/benchmark.toml \
  --oracle
```

Every generated matrix records the benchmark ID, version, and manifest hash.
The publisher treats that identity as part of benchmark compatibility, so
results from modified manifests cannot be silently combined.

Before launching its first worker, the matrix runner copies the host runner,
helper scripts, selected scenario packs, custom adapter, custom payload, and
local Claux binary into `execution-snapshot/` inside the matrix result
directory. Every worker executes those immutable copies. Switching branches or
editing a live scenario while a matrix runs cannot change later workers.

Scenario-pack snapshots exclude Git metadata and Python bytecode caches. The
full pack hash protects frozen execution and resume integrity, while separate
hashes for each selected scenario define result compatibility. Adding or
editing an unselected scenario therefore does not split an otherwise identical
cohort. Changing a selected scenario does. Pack versions and source Git commits
remain recorded as provenance without overriding those content-scoped hashes.

`benchmark.json` and `execution-snapshot/manifest.json` record SHA-256 hashes
for the staged harness, packs, and optional agent artifacts. Runtime environment
files are copied once into a temporary mode-0600 directory for the duration of
the matrix, hashed for compatibility, and deliberately excluded from retained
artifacts. The benchmark publisher refuses to combine matrices whose execution
snapshots differ.

## Explore local results

The result catalog turns retained host-matrix summaries into a rebuildable
SQLite index. The source `summary.json` files remain authoritative; the
database under `jobs/` is disposable and ignored by Git.

Import every supported host-native result in the local archive:

```sh
python -m integrations.host.result_catalog import jobs
```

Compare models on the newest exactly compatible cohort for a scenario:

```sh
python -m integrations.host.result_catalog compare \
  --scenario 024-discourse-interrupted-deploy
```

The comparison reports evaluated and unavailable trials separately, a Wilson
95 percent confidence interval, known spend, and cost per durable repair. Its
compatibility identity includes the selected scenario version and content
hash, host harness snapshot, benchmark manifest, adapter and payload hashes,
Claux release, and timeout. Full pack revisions remain provenance. Legacy
matrices without selected-scenario hashes retain whole-pack compatibility.
When the archive contains multiple cohorts, the command warns and uses the
newest one. List or select cohorts explicitly when investigating historical
changes:

```sh
python -m integrations.host.result_catalog compare \
  --scenario 024-discourse-interrupted-deploy \
  --list-cohorts

python -m integrations.host.result_catalog compare \
  --scenario 024-discourse-interrupted-deploy \
  --compatibility 3247657883e1
```

Re-importing a matrix updates its derived rows without duplicating trials.
Infrastructure failures are retained for audit but excluded from model pass
rates. Older isolated-container summaries use a different suite and are
reported as unsupported rather than mixed with host-native evidence.

## Publish benchmark results

The benchmark publisher imports one or more compatible matrix summaries into a
tracked DateVer snapshot, then generates the current site and Markdown record:

```sh
python integrations/host/publish_benchmarks.py import \
  --version 20260809.0.0 \
  --annotations benchmark-data/annotations/20260809.0.0.json \
  jobs/host-matrix-first/summary.json \
  jobs/host-matrix-second/summary.json
```

Before combining results it requires the same suite, host harness, selected
scenario content, attempt count, agent timeout, adapter, and Claux release.
Full scenario-pack revisions, source matrix names, and Replaybook commits
remain visible as provenance. The tracked release contains normalized result
data, not local paths, transcripts, credentials, or VM logs.

Rebuild generated files without the original `jobs/` directories:

```sh
python integrations/host/publish_benchmarks.py build
```

The build also creates a sanitized public catalog at
`benchmark-data/catalog.json`, mirrors it into `docs/`, and generates the
interactive benchmark explorer. The explorer can drill into one published
DateVer release, scenario, or model without mixing incompatible releases.
Local paths, transcripts, credentials, and unpublished SQLite rows never enter
the public catalog.

CI uses the corresponding `check` command to reject stale generated pages.
Importing a later DateVer release keeps earlier snapshots in the generated
history. Optional annotations provide display names, editorial observations,
and explicit post-run corrections with their original values and reasons.

## Run Codex

The bundled Codex adapter uses `codex exec` and captures its JSON event stream,
final message, and token usage. Locate the native binary installed with your
local Codex CLI, then create a temporary mode-0600 environment file containing
a sanitized copy of the current login cache:

```sh
codex_binary="$(integrations/host/find-codex-binary.sh)"
codex_env="$(integrations/host/prepare-codex-env.sh)"
```

Run one trial with ChatGPT-managed Codex authentication:

```sh
python integrations/host/run_host_matrix.py \
  --scenario 001-nginx-502-host \
  --models gpt-5.6-sol \
  --agent-adapter integrations/host/adapters/codex.sh \
  --agent-payload "$codex_binary" \
  --agent-env-file "$codex_env" \
  --agent-name codex \
  --attempts 1 \
  --concurrency 1
```

Remove the temporary environment file after the run. The helper replaces the
refresh token before staging the authentication cache, and the adapter uses an
isolated, disposable `CODEX_HOME`. The VM therefore cannot rotate or expose
the refresh credential used by your local login. If the access token has
expired, use Codex locally once and recreate the environment file. For API-key
automation, provide `CODEX_API_KEY=...` in the environment file instead. The
adapter deliberately ignores the user's Codex configuration and rules so local
MCP servers, hooks, and preferences do not change benchmark behavior.

## Build another adapter with the skill

Replaybook ships a Codex skill that guides adapter discovery, implementation,
credential handling, normalization, and verification. Install it from a clone:

```sh
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/replaybook-add-harness" \
  ~/.codex/skills/replaybook-add-harness
```

Then ask Codex:

```text
Use $replaybook-add-harness to integrate <harness> with Replaybook.
```

The skill includes a shell adapter template and a deterministic validator for
the normalized result contract. It requires a fake-CLI test before recommending
a paid model run.

Each trial receives adjacent SSH and HTTP ports starting at `--base-port`.
Results are written under `jobs/host-matrix-*`, including per-trial logs,
result and transcript paths, benchmark metadata, failure categories, model
aggregates with token, cost, and execution-timing totals, and
scenario-version-aware aggregates. Claux transcript v2 recordings add each
provider round and tool call with monotonic start offsets and durations. The
matrix reports median model rounds, model time, tool calls, tool time, time to
the first write-capable tool, and time remaining after that tool. Older and
third-party adapters remain valid and simply report no execution recording.
Evaluation failures are valid matrix results. Authentication failures and
provider or harness errors that occur before meaningful inference are recorded
as unavailable attempts and excluded from pass-rate denominators. Once an
agent has completed a model round or invoked a tool, later provider
interruptions and runtime errors are evaluated failures. Provider-side content
policy rejections and malformed protocol responses, including corrupted
reasoning signatures, retain distinct `provider_policy_rejection` and
`provider_protocol_error` categories instead of being collapsed into
`agent_runtime_error`. Exhausting a model response's output-token limit is also
an evaluated failure. The command exits
nonzero when a worker produces no valid result or a requested attempt is
unavailable, because that matrix is incomplete rather than evidence of model
failure.

The matrix passes `--agent-timeout-seconds` to every worker and records the
chosen value in `benchmark.json`. A model that exceeds the limit is terminated
inside its VM and recorded with `failure_category: "agent_timeout"`.

List available scenarios and their versions with:

```sh
python integrations/host/run_host_matrix.py --list-scenarios
```

Results are written under `jobs/host-native-*`. Adapters may include a complete
tool transcript alongside their normalized result. `result.json` records the
host harness version, scenario version, agent duration, usage, optional
execution recording, and separate
immediate, service-restart, and host-reboot verification outcomes. A result is
comparable only with runs using the same host harness and scenario versions.
Host harness version 2 is the first version that guarantees the reference
repair is absent from model VMs. Version 3 introduces typed scenario manifests,
generic lifecycle phases, persistent controller state, and structured verifier
failure categories. Version 4 introduces the harness adapter contract and
generic result and transcript artifacts. Version 5 distinguishes unavailable
provider or harness attempts from evaluated repairs and excludes them from
model pass-rate denominators. Version 11 carries sanitized model-round and tool
timings from supporting adapters into run and matrix results. Version 13
distinguishes failures before inference from interruptions after an agent has
begun work and records output-token exhaustion as an evaluated agent failure.
Version 14 tells agents their wall-clock budget and runs the full external
verification lifecycle after a timeout. A durable repair left by a timed-out
agent remains an `agent_timeout` failure, but is recorded separately under
`verification.after_agent_timeout` and summarized by the matrix runner.
Version 15 builds a dedicated Nix store image for every incident VM. Agents
can inspect only the selected scenario's closure instead of unrelated
derivations accumulated in the controller host's Nix store.
Version 19 distinguishes a known harness-device boot failure from a repair that
does not survive reboot. If the verifier-controlled guest cannot remount the
harness-provided Nix store image, the trial is unavailable and excluded from
the model's pass rate. Version 20 distinguishes provider policy rejections and
provider protocol failures from agent runtime errors. Their trial status still
depends on whether meaningful inference began: failures before progress are
unavailable, while failures after progress are evaluated execution-lane
failures.

The controller owns reboot verification. Agents are instructed not to reboot
the host during their session. If an SSH session ends with status 255 and the
VM console confirms a reboot, the result records
`failure_category: "agent_rebooted_host"`.

The controller captures the agent result and transcript before verification
restarts the repaired services or reboots the host. Usage therefore remains
available even when a broken repair prevents the VM from returning. A VM that
does not complete the verifier-controlled reboot records
`failure_category: "host_reboot_failed"`; a host that returns without its
required services records `failure_category: "services_failed_after_reboot"`.
If the guest console instead shows that the harness-provided Nix store device
disappeared during reboot, the result is unavailable with
`failure_category: "guest_boot_infrastructure_failed"`.

Scenario verifiers may also return a specific failure category. The Sidekiq
scenario records `failure_category: "backlog_not_recovered"` when the agent
repairs future processing but abandons or deletes work that was already queued.
The migration scenario records `failure_category: "migration_not_applied"`
when the application appears healthy without the deployed schema migration.
The poison-pill scenario records `failure_category: "poison_not_quarantined"`
when valid work or the bad job is discarded. Declarative scenarios report the
category named by the failed manifest assertion; the Rails pool scenario uses
`failure_category: "database_pool_exhausted"`.

Harness credentials are written to a mode-0600 file inside the disposable VM,
used only for the adapter process, and destroyed with the VM. The agent runs as
root and can damage its own evaluator access, but it cannot affect the local
controller or host beyond the forwarded SSH and HTTP connections.
