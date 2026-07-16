# Hosted execution

Replaybook can run a training session on a dedicated Linux VM and give the
trainee a forced-command SSH credential. The trainee gets the normal
Replaybook workstation and real Docker-backed services; the controller stages
the scenario, tracks its state, expires it, and removes its access key.

## Milestone task list

- [x] Remote single-session prototype on a dedicated VM
- [x] Execution-backend boundary with local Docker and remote VM backends
- [x] Authenticated control plane for create, status, delete, and expiry

Provider-specific VM creation is intentionally outside Replaybook. Provision a
disposable VM with Terraform, cloud-init, an internal VM service, or the cloud
provider of your choice, then point Replaybook at its SSH destination. The
current control plane enforces one live session on its configured VM.

For a concrete two-host installation using an existing trusted controller VPS
and a disposable Fornex worker, see the [deployment kit](../deploy/README.md).

## Security boundary

The trainee workstation mounts the VM's Docker socket. A trainee must therefore
be treated as owning the entire VM. Never point hosted execution at a shared
Docker host, a production machine, or a VM containing credentials or unrelated
workloads.

Use these boundaries:

- one disposable VM for one concurrent trainee session;
- a dedicated, unprivileged SSH user that can access Docker;
- no cloud instance credentials or secrets on the VM;
- trusted scenario packs only (scenario scripts execute on the VM);
- the control API on loopback, or behind an authenticated TLS reverse proxy;
- network policy limiting the VM to the control plane and expected dependencies.

The participant key is restricted by OpenSSH and forced to run exactly one
`replaybook hosted-run` command. Port forwarding, agent forwarding, X11
forwarding, and arbitrary SSH commands are disabled. A PTY is explicitly
allowed because the training shell is interactive. This limits how the key can
be used; it does not change the Docker-socket trust boundary.

## VM prerequisites

The dedicated VM needs:

- Linux with `sshd`, `tar`, and `ssh-keygen` support;
- Docker with the Compose plugin;
- the same Replaybook release installed as the controller;
- an SSH user authorized for the controller and able to use Docker;
- outbound image-registry access required by the selected scenario.

The controller verifies Replaybook and Docker Compose before staging a session,
then validates the staged scenario on the VM before installing the participant
key.

## Direct remote session

For a one-off pilot, attach directly from the controller:

```bash
replaybook remote 001-nginx-502 \
  --host replaybook@training-vm.example.com \
  --sla 15
```

Replaybook performs the following lifecycle:

1. generates a unique Ed25519 participant key;
2. streams the scenario to a session-specific directory over SSH;
3. validates it on the VM;
4. installs a restricted forced-command key;
5. attaches the local terminal to the participant SSH session;
6. reads the remote outcome and destroys the staged environment and key.

## Control plane

Run the control plane on a trusted controller host. The bearer token must be at
least 24 characters. The default bind address is loopback.

```bash
export REPLAYBOOK_CONTROL_TOKEN="$(openssl rand -hex 32)"

replaybook serve \
  --host replaybook@training-vm.example.com \
  --scenarios-dir ~/.local/share/replaybook/scenarios \
  --bind 127.0.0.1:8080 \
  --default-ttl 60
```

Create a session using an installed scenario ID:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $REPLAYBOOK_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"001-nginx-502","sla_minutes":15,"ttl_minutes":45}' \
  http://127.0.0.1:8080/v1/sessions > session.json

id="$(jq -r .id session.json)"
jq -r .private_key session.json > "replaybook-$id.key"
chmod 600 "replaybook-$id.key"
jq -r .connect session.json
```

The private key is returned only by the create response. Store it like a
password and delete it after the exercise.

Read status or destroy the session:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $REPLAYBOOK_CONTROL_TOKEN" \
  "http://127.0.0.1:8080/v1/sessions/$id"

curl --fail-with-body -X DELETE \
  -H "Authorization: Bearer $REPLAYBOOK_CONTROL_TOKEN" \
  "http://127.0.0.1:8080/v1/sessions/$id"
```

The API supports:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | unauthenticated liveness check |
| `POST` | `/v1/sessions` | stage a scenario and issue participant access |
| `GET` | `/v1/sessions/:id` | refresh and return session status |
| `DELETE` | `/v1/sessions/:id` | stop, revoke, and destroy a session |

Session metadata persists under
`~/.local/share/replaybook/control/sessions.json`. The expiry loop checks every
30 seconds. A failed cleanup keeps the VM occupied instead of allowing a second
session onto an uncertain host.

## Current boundary

This milestone is deliberately a single-VM control plane. A VM pool,
provider-specific create/destroy adapters, browser terminals, organization
accounts, and centralized transcript storage are later layers. The current
shape is enough to run an authentic internal pilot over SSH without weakening
the isolation model.
