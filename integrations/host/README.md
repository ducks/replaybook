# Host-native evaluation

This integration evaluates an infrastructure agent on a real disposable Linux
host. The agent does not receive a Docker socket and does not manage sibling
containers.

The local controller builds and boots a scenario-selected NixOS VM. Three
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

Each scenario supplies its NixOS topology, incident instruction, reference
repair, broken-state preflight, and external verifier. The controller verifies
the repair through the user-facing HTTP boundary.

Claux runs directly as root on that VM and investigates with normal Linux tools
such as `systemctl`, `journalctl`, `ps`, `ss`, and the filesystem. The controller
remains outside the incident host and verifies the HTTP endpoint after the
repair, after restarting both services, and after rebooting the entire VM.
The worker explicitly sets Claux's native-tool and Bash filesystem policies to
`unrestricted`; infrastructure repair requires writes outside a source
workspace, and the VM itself is the disposable security boundary.

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
`REPLAYBOOK_HOST_CLAUX_BINARY` to evaluate a local binary instead of downloading
the default released Claux version. Claux receives 900 seconds by default;
override it with `--agent-timeout-seconds`.

Host boot, reboot, and service-readiness checks use wall-clock deadlines, so
repeated SSH connection attempts cannot extend a failed trial indefinitely.
When Claux supports graceful one-shot signal cancellation, an agent timeout
also preserves its partial messages, tool trace, token usage, and known cost.

## Run a model matrix

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
  --attempts 3 \
  --concurrency 2
```

Each trial receives adjacent SSH and HTTP ports starting at `--base-port`.
Results are written under `jobs/host-matrix-*`, including per-trial logs,
result and transcript paths, benchmark metadata, failure categories, model
aggregates with token and cost totals, and scenario-version-aware aggregates.
Evaluation failures are valid matrix results; the command exits nonzero only
when a worker fails to produce a valid result.

The matrix passes `--agent-timeout-seconds` to every worker and records the
chosen value in `benchmark.json`. A model that exceeds the limit is terminated
inside its VM and recorded with `failure_category: "agent_timeout"`.

List available scenarios and their versions with:

```sh
python integrations/host/run_host_matrix.py --list-scenarios
```

Results are written under `jobs/host-native-*`. Claux runs include its native
one-shot JSON output and complete tool transcript. `result.json` records the
scenario version, agent duration, usage, and separate immediate,
service-restart, and host-reboot verification outcomes. A result is comparable
only with runs using the same scenario version.

The controller owns reboot verification. Agents are instructed not to reboot
the host during their session. If an SSH session ends with status 255 and the
VM console confirms a reboot, the result records
`failure_category: "agent_rebooted_host"`.

The controller captures Claux's result and transcript before verification
restarts the repaired services or reboots the host. Usage therefore remains
available even when a broken repair prevents the VM from returning. A VM that
does not complete the verifier-controlled reboot records
`failure_category: "host_reboot_failed"`; a host that returns without its
required services records `failure_category: "services_failed_after_reboot"`.

Scenario verifiers may also return a specific failure category. The Sidekiq
scenario records `failure_category: "backlog_not_recovered"` when the agent
repairs future processing but abandons or deletes work that was already queued.
The migration scenario records `failure_category: "migration_not_applied"`
when the application appears healthy without the deployed schema migration.

The OpenRouter credential is written to a mode-0600 file inside the disposable
VM, used only for the Claux process, and destroyed with the VM. The agent runs
as root and can damage its own evaluator access, but it cannot affect the local
controller or host beyond the forwarded SSH and HTTP connections.
