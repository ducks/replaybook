# Host-native evaluation spike

This spike evaluates an infrastructure agent on a real disposable Linux host.
The agent does not receive a Docker socket and does not manage sibling
containers.

The local controller builds and boots a scenario-selected NixOS VM. Two
scenarios are currently available:

- `001-nginx-502-host`: Nginx points at the wrong backend port.
- `013-sidekiq-wrong-redis`: a healthy Ruby web service enqueues checkout
  confirmation jobs into Redis database 0 while Sidekiq watches database 1.
  Successful jobs write a durable completion record to PostgreSQL. Its verifier
  requires both new jobs and the pre-existing backlog to complete, so deleting
  or abandoning queued work does not pass.

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

## Run Claux

With `OPENROUTER_API_KEY` set:

```sh
integrations/host/run-host-native.sh \
  --scenario 013-sidekiq-wrong-redis \
  --model deepseek/deepseek-v4-flash
```

Use `--ssh-port` and `--http-port` when running workers concurrently. Set
`REPLAYBOOK_HOST_CLAUX_BINARY` to evaluate a local binary instead of downloading
the default released Claux version.

Results are written under `jobs/host-native-*`. Claux runs include its native
one-shot JSON output and complete tool transcript. `result.json` records the
agent duration, usage, and separate immediate, service-restart, and host-reboot
verification outcomes.

The controller owns reboot verification. Agents are instructed not to reboot
the host during their session. If an SSH session ends with status 255 and the
VM console confirms a reboot, the result records
`failure_category: "agent_rebooted_host"`.

Scenario verifiers may also return a specific failure category. The Sidekiq
scenario records `failure_category: "backlog_not_recovered"` when the agent
repairs future processing but abandons or deletes work that was already queued.

The OpenRouter credential is written to a mode-0600 file inside the disposable
VM, used only for the Claux process, and destroyed with the VM. The agent runs
as root and can damage its own evaluator access, but it cannot affect the local
controller or host beyond the forwarded SSH and HTTP connections.
