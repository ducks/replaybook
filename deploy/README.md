# Two-host deployment

This kit deploys Replaybook without putting application state on an operator's
laptop. It has two deliberately separate trust zones:

```text
api.replaybook.dev                 training.replaybook.dev
trusted controller                disposable worker
------------------                -----------------
replaybook serve       --SSH-->   replaybook + Docker
scenario packs                     trainee sessions
API token + private key            controller public key only
```

The controller can share a VPS with the Replaybook website. The worker must not
share a VPS with anything valuable: a trainee can access its Docker socket and
must be treated as owning that entire machine.

## Supported starting point

- Controller: a trusted Debian/Ubuntu VPS using systemd.
- Worker: a fresh Fornex Ubuntu 24.04 x86-64 KVM VPS.
- DNS: `api.replaybook.dev` points to the controller and
  `training.replaybook.dev` points to the worker.
- Replaybook: the same tagged release on both hosts.

The scripts are intentionally not a generic server-management framework. They
configure the known two-host pilot and fail closed on unsupported worker
operating systems.

## 1. Prepare DNS and network policy

Create the two DNS records before enabling TLS. At the provider firewall:

- controller: allow HTTPS (`443`) and administrative SSH;
- worker: allow SSH (`22`) for the controller and expected trainees;
- worker: do not expose scenario container ports to the public internet.

Prefer the provider firewall over UFW for the worker. Published Docker ports
can bypass UFW rules; Docker documents this behavior in its
[Ubuntu installation notes](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).

## 2. Prepare the controller

Check out the same release you intend to install, then run:

```bash
sudo env \
  REPLAYBOOK_VERSION=v<release> \
  WORKER_HOST=training.replaybook.dev \
  SCENARIO_REPO=ducks/replaybook-scenarios \
  ./deploy/controller/bootstrap.sh
```

The script:

1. installs the checksum-verified release binary;
2. creates the `replaybook-control` system account;
3. generates separate service and worker-administration Ed25519 keys;
4. generates and stores the API bearer token;
5. installs a sandboxed systemd unit;
6. installs or updates the configured trusted scenario pack.

It does not start the service until the worker host key has been verified. The
private service key remains under `/var/lib/replaybook/.ssh`; only its public
half is copied to the worker. A separate root-owned administrative key is
created at `/root/.ssh/replaybook-worker-admin`. The API token is stored in
`/etc/replaybook/control.env` with restricted permissions.

Releases made before `SHA256SUMS` was added can only be installed by explicitly
setting `ALLOW_UNVERIFIED_DOWNLOAD=1`. New deployments should use a release that
contains the checksum file.

## 3. Bootstrap the Fornex worker

From the controller, copy the deployment kit and the controller public key over
the initial root access supplied by Fornex:

```bash
ssh root@training.replaybook.dev 'mkdir -p /root/replaybook-deploy'
scp install.sh root@training.replaybook.dev:/root/replaybook-deploy/
scp -r deploy root@training.replaybook.dev:/root/replaybook-deploy/
scp /var/lib/replaybook/.ssh/id_ed25519.pub \
  root@training.replaybook.dev:/root/replaybook-controller.pub
scp /root/.ssh/replaybook-worker-admin.pub \
  root@training.replaybook.dev:/root/replaybook-admin.pub
```

Then bootstrap the worker:

```bash
ssh -t root@training.replaybook.dev \
  'cd /root/replaybook-deploy && sudo env \
    REPLAYBOOK_VERSION=v<release> \
    CONTROLLER_KEY_FILE=/root/replaybook-controller.pub \
    ADMIN_KEY_FILE=/root/replaybook-admin.pub \
    ./deploy/worker/bootstrap.sh'
```

The worker installs Docker from Docker's official apt repository, installs the
same Replaybook release, creates the `replaybook` execution user, installs the
dedicated root-administration key, disables SSH password and forwarding
features, and prints its Ed25519 host-key fingerprint.

Do not run the worker bootstrap during an active exercise: it intentionally
replaces the execution user's `authorized_keys` file and revokes outstanding
participant keys.

## 4. Verify trust and start the controller

Compare the fingerprint printed by the worker with the one visible through the
Fornex console. On the controller, pass that exact value to:

```bash
sudo ./deploy/controller/trust-worker.sh \
  training.replaybook.dev \
  SHA256:<worker-fingerprint>
```

This pins the worker host key, verifies noninteractive SSH, checks Replaybook
and Docker Compose remotely, starts `replaybook-control.service`, and calls its
loopback health endpoint.

Useful controller commands:

```bash
sudo systemctl status replaybook-control
sudo journalctl -u replaybook-control -f
sudo cat /etc/replaybook/control.env
```

## 5. Terminate TLS

Keep Replaybook bound to `127.0.0.1:8080`. Use the example matching the reverse
proxy already installed on the controller:

- [`controller/Caddyfile.example`](controller/Caddyfile.example)
- [`controller/nginx.conf.example`](controller/nginx.conf.example)

Do not publish port 8080. The bearer token protects the API, while the reverse
proxy supplies HTTPS.

After configuring TLS:

```bash
curl --fail https://api.replaybook.dev/healthz
```

Create requests use the bearer token from `/etc/replaybook/control.env`, as
documented in [`../docs/HOSTING.md`](../docs/HOSTING.md).

## Updating

Never allow the controller and worker versions to drift. With no active
session, check out the new release of this deployment kit and rerun both
bootstrap scripts with the same `REPLAYBOOK_VERSION`. Verify the worker host
fingerprint again, then restart the controller.

Rebuild the worker after untrusted use or suspected compromise. Updating
packages in place is not a substitute for restoring the execution boundary.
