# Host-native evaluation spike

This spike evaluates an infrastructure agent on a real disposable Linux host.
The agent does not receive a Docker socket and does not manage sibling
containers.

The local controller builds and boots a NixOS VM containing:

- `checkout-backend.service`, a Python HTTP backend listening on port 3000
- `incident-nginx.service`, Nginx using mutable configuration under
  `/etc/replaybook`
- A broken upstream configuration pointing Nginx at port 3001
- SSH access for the agent

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

## Run Claux

With `OPENROUTER_API_KEY` set:

```sh
integrations/host/run-host-native.sh \
  --model deepseek/deepseek-v4-flash
```

Use `--ssh-port` and `--http-port` when running workers concurrently. Set
`REPLAYBOOK_HOST_CLAUX_BINARY` to evaluate a local binary instead of downloading
the default released Claux version.

Results are written under `jobs/host-native-*`. Claux runs include its native
one-shot JSON output and complete tool transcript. `result.json` records the
agent duration, usage, and separate immediate, service-restart, and host-reboot
verification outcomes.

The OpenRouter credential is written to a mode-0600 file inside the disposable
VM, used only for the Claux process, and destroyed with the VM. The agent runs
as root and can damage its own evaluator access, but it cannot affect the local
controller or host beyond the forwarded SSH and HTTP connections.
