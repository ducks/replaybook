# Harbor evaluation spike

This directory contains a proof of concept for running a Replaybook incident as
a graded [Harbor](https://harborframework.com/) task. It deliberately lives
outside Replaybook's CLI while the integration boundary is being evaluated.

The spike converts twelve Replaybook scenarios into deterministic tasks:
`001-nginx-502`, `002-postgres-rejecting-connections`, `003-missing-env-var`,
`004-disk-full`, `005-oom-kill`, `006-sidekiq-cant-connect`,
`007-packet-loss`, `008-connection-pool-exhaustion`, and
`009-phantom-backend`, plus the harder `010-stale-auth-secret`,
`011-partial-rollout`, and `012-retry-storm` tasks. Harbor runs the agent in a `main` workstation container
alongside the incident's services. The workstation mounts the local Docker
socket, matching Replaybook's current workstation security model, so the agent
can inspect and repair sibling services with `docker ps`, `docker logs`,
`docker exec`, and `docker cp`.

## Prerequisites

- Docker with the Compose plugin and Buildx 0.17 or later
- Harbor 0.20 or later

Install Harbor with:

```sh
uv tool install harbor
```

## Validate the task

Run the known-good solution first:

```sh
HARBOR_TELEMETRY=off harbor run \
  -p integrations/harbor/tasks/001-nginx-502 \
  -a oracle
```

The trial should finish with reward `1`. A no-op trial should finish with
reward `0`:

```sh
HARBOR_TELEMETRY=off harbor run \
  -p integrations/harbor/tasks/001-nginx-502 \
  -a nop
```

The spike includes a minimal Claux adapter. With an OpenRouter key exported in
your shell, run:

```sh
PYTHONPATH="$PWD" HARBOR_TELEMETRY=off harbor run \
  -p integrations/harbor/tasks/001-nginx-502 \
  -a integrations.harbor.claux_agent:ClauxAgent \
  -m deepseek/deepseek-v4-flash \
  --agent-env OPENROUTER_API_KEY="$OPENROUTER_API_KEY"
```

Use `--ak release_tag=v...` to evaluate another published Claux version. The
adapter installs the release in the incident workstation, initializes a
credential-free OpenRouter configuration, runs Claux headlessly with bypass
permissions, and saves its machine-readable output and native tool transcript
under Harbor's agent logs. It reports input, output, and cache tokens plus
provider-reported or estimated cost to Harbor, and translates the transcript
to Harbor's ATIF trajectory format.

## Run the three-agent comparison

After the individual smoke tests pass, run three attempts each for Codex, Claude
Code, and Claux against the nginx task with:

```sh
claude setup-token
nu integrations/harbor/run-three-agent-smoke.nu
```

The launcher reads `CLAUDE_CODE_OAUTH_TOKEN` and `OPENROUTER_API_KEY` from the
environment when set, otherwise prompts for them privately. It selects a
compatible Nix-provided Buildx without modifying the user Docker installation
and starts the job defined in
`jobs/three-agent-smoke.yaml`. The job runs nine trials total, with one trial
executing at a time. Local concurrency is intentionally disabled because the
current workstation Docker socket can see other trials and unrelated host
containers. Use a dedicated disposable VM before enabling parallel trials.

The default pairings are:

- Codex with `gpt-5.6-sol`
- Claude Code with `claude-sonnet-5`
- Claux with `deepseek/deepseek-v4-flash` through OpenRouter

The isolated matrix launcher can select any converted scenario:

```sh
nu integrations/harbor/run-isolated-matrix.nu \
  --scenario 002-postgres-rejecting-connections

nu integrations/harbor/run-isolated-matrix.nu \
  --scenario 003-missing-env-var

# Run the original nine-scenario core set
nu integrations/harbor/run-isolated-matrix.nu --scenario-set core

# Run the harder security, topology, and latency set
nu integrations/harbor/run-isolated-matrix.nu --scenario-set hard

# Run all twelve scenarios for each selected agent
nu integrations/harbor/run-isolated-matrix.nu --scenario-set all

# Compare another OpenRouter model through Claux
nu integrations/harbor/run-isolated-matrix.nu \
  --all-scenarios --agent claux --claux-model moonshotai/kimi-k3
```

Use `--oracle --attempts 1` first for a credential-free verifier smoke test.

## Run on a disposable local worker

On a Linux host with KVM, QEMU, Nix, and an Ed25519 SSH key, validate the task
inside a disposable NixOS VM. Pass normal `harbor run` arguments after `--`:

```sh
bash integrations/harbor/run-isolated.sh -- \
  --config integrations/harbor/jobs/oracle-smoke.yaml \
  --yes
```

The launcher builds and boots a minimal VM, forwards SSH to localhost port
`22222`, stages only the Harbor integration, installs Harbor, runs the requested
evaluation, retrieves the result, and terminates the VM. Override the defaults
with `REPLAYBOOK_WORKER_SSH_KEY` and
`REPLAYBOOK_WORKER_SSH_PORT`.

The original oracle shortcut remains available as
`run-isolated-oracle.sh`. To run a complete job configuration and forward only
the credentials it needs:

```sh
bash integrations/harbor/run-isolated.sh \
  --codex-auth \
  --env CLAUDE_CODE_OAUTH_TOKEN \
  --env OPENROUTER_API_KEY \
  -- --config integrations/harbor/jobs/three-agent-smoke.yaml --yes
```

`--env` accepts an environment variable name, validates that it is set, and
transfers its value in a mode-`0600` file rather than exposing it in the SSH
command line. `--codex-auth` copies the configured Codex auth file only for the
lifetime of the VM. Set `REPLAYBOOK_CODEX_AUTH_FILE` to override its path.

For the clean comparison run, launch one VM for every model attempt:

```sh
nu integrations/harbor/run-isolated-matrix.nu
```

This runs three attempts each for Codex, Claude Code, and Claux. It uses at most
two simultaneous VMs by default because each worker has 4 GiB of memory. Pass
`--concurrency N` to change the pool size. Every attempt receives a distinct SSH
port and result directory; the launcher writes an aggregate `summary.json` after
all workers finish. Validate the worker pool without model credentials or API
cost using `--oracle --attempts 2`. Run or recover a single model with
`--agent codex`, `--agent claude`, or `--agent claux`.

Use `--scenario-set core`, `--scenario-set hard`, or `--scenario-set all` to
run a named scenario set in one matrix. `--all-scenarios` remains a compatible
alias for the full set. The selected attempt count applies to every
agent/scenario pair. Claux defaults to
`deepseek/deepseek-v4-flash`; pass `--claux-model <openrouter/model-id>` to use
another OpenRouter model with the same adapter and verifier path.

If a worker run completes but summary generation fails, recover the report
without rerunning the trials:

```sh
nu integrations/harbor/recover-matrix-summary.nu \
  jobs/isolated-matrix-YYYY-MM-DD__HH-MM-SS.XXXXXX
```

The recovery command reads the saved worker results and regenerates
`summary.json`, including rewards, costs, mean and median trial durations, and
structured failure categories. Verifiers distinguish repairs that never
recovered the service (`repair_incomplete`), changed the expected topology
(`topology_changed`), could not be restarted (`restart_failed`), or failed
after restart (`repair_not_durable`). Agent exceptions are reported as
`timeout` when identifiable and `agent_error` otherwise. Results created before
category capture remain recoverable as `uncategorized`.

The Nushell launcher snapshots the Bash worker runner before starting the pool,
so edits to the checkout cannot alter already-running trials.

## Compare matrix results

Combine any number of completed matrix directories into a single report:

```sh
integrations/harbor/report_matrix_results.py \
  jobs/isolated-matrix-2026-08-05__01-07-01.AAAAAA \
  jobs/isolated-matrix-2026-08-05__01-20-07.BBBBBB \
  jobs/isolated-matrix-2026-08-05__01-32-12.CCCCCC
```

The default Markdown report compares durable repairs, agent errors, median
trial time, reported cost, per-scenario results, and failure categories. Write
it to a file with `--output report.md`, or produce machine-readable output with
`--format json --output report.json`. Paths may point to either a matrix
directory or its `summary.json` file. Repeating the same job in multiple input
summaries does not count it twice.

New matrix runs save the selected scenario set and Replaybook commit in
`benchmark.json` and carry that metadata into `summary.json`. The comparison
command also accepts older summaries without this metadata, inferring the
scenario set from their runs while leaving unavailable provenance explicit.

## Analyze agent trajectories

Claux runs retain their exact tool inputs and outputs as an ATIF trajectory.
Summarize one trial or a complete matrix directory with:

```sh
integrations/harbor/analyze_trajectory.py \
  jobs/isolated-matrix-YYYY-MM-DD__HH-MM-SS.XXXXXX
```

The report shows tool and error counts, output volume, the first detected
system mutation, mutation categories, high-risk container replacement and
image rebuild signals, the largest tool outputs, usage, cost, and the verifier
outcome. Use `--format json` for further analysis or `--output FILE` to retain
the report.

This is a reusable isolated job runner, not yet a Harbor environment adapter.
The whole Harbor job runs inside the worker, so its Docker socket owns only
that disposable VM.

## What this proves

- Harbor can represent Replaybook's multi-container workstation topology.
- A fault can be selected deterministically by baking its broken state into a
  task variant.
- Replaybook's objective recovery check maps to a Harbor verifier reward.
- The converted service verifiers check that recovery survives a restart, so a
  temporary in-container workaround does not count as a durable fix.
- Harbor can run a reference solution without exposing it to a normal agent.

## Known limitations

- This task is a hand-built conversion, not an exporter.
- The Docker socket makes the agent root-equivalent on the Docker host. Run it
  only on a disposable or trusted machine.
- The verifier shares the agent environment. This is sufficient for an honest
  harness comparison, but a public adversarial benchmark needs a controller-
  held verifier that the worker cannot inspect or modify.
- The Claux adapter saves the native transcript as `claux-transcript.json` and
  translates its ordered tool calls and exact outputs into Harbor's
  `trajectory.json`. Claux does not currently emit assistant reasoning between
  tool calls, so that reasoning is not present in the ATIF trajectory.
- Claux transcripts contain raw tool inputs and outputs. They may contain
  credentials or other sensitive incident data and should be handled like the
  systems being evaluated.

The next integration step is an exporter that generates Harbor task definitions
from Replaybook scenarios and fault variants rather than maintaining copied
task definitions by hand.
