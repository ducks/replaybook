# replaybook

Incident replay trainer. Get paged, fix real broken infrastructure, win.

Each scenario is a Docker environment with a fault injected. You're dropped
into a workstation container on the incident's network - a jumphost with the
docker CLI, where `docker exec` is your ssh into the broken services. The
terminal splits - shell on the left, HUD on the right showing the incident
page, SLA countdown, and hints. Diagnose and fix with real tools, inside the
real services. The engine polls a health check in the background - when it
goes green, you're done.

Built to turn post-mortems into playable scenarios. New engineers build muscle
memory on the actual failure modes your team has hit, not simulations.

Replaybook can also evaluate agents operating those systems. The same scenarios
run as reproducible Harbor tasks inside disposable NixOS workers. An agent must
inspect the live services, repair the deployed system, and pass a hidden
verifier after the affected services restart. This measures durable operational
reasoning, not just whether an agent can produce a plausible patch.

The evaluation stack is deliberately composable:

- Replaybook supplies realistic incidents, fault injection, and verifiers.
- [Harbor](https://github.com/laude-institute/harbor) runs agents and records
  rewards, tokens, and costs.
- Nix provides isolated, reproducible workers with a fresh Docker environment.
- [Claux](https://github.com/ducks/claux) provides a lightweight OpenRouter
  agent adapter with usage and cost reporting.

The result is a small harness for comparing agents on diagnosis, remediation,
durability, cost, and (with the raw trial results) execution time.

## Install

```bash
cargo install replaybook
```

Requires Docker. Prebuilt binaries for linux-x86_64, linux-arm64,
macos-x86_64, and macos-arm64 are on the
[releases page](https://github.com/ducks/replaybook/releases).

## Getting started

```bash
# add the official scenario pack
replaybook add ducks/replaybook-scenarios

# see what's available
replaybook list

# run your first scenario
replaybook run 001-nginx-502
```

Both `replaybook` and `replay` are installed - use whichever you prefer.

## Hosted sessions

Replaybook can stage a scenario on a dedicated disposable Linux VM and issue a
restricted SSH credential to a trainee. For a one-off session:

```bash
replaybook remote 001-nginx-502 \
  --host replaybook@training-vm.example.com
```

`replaybook serve` adds an authenticated control API for creating, inspecting,
expiring, and destroying sessions. Hosted execution is intentionally limited
to one live session per configured VM: the trainee workstation has the Docker
socket and must be treated as owning that VM. See
[Hosted execution](docs/HOSTING.md) for setup, API examples, and the security
model. A repeatable [two-host deployment kit](deploy/README.md) configures a
trusted controller and a disposable Fornex-compatible Ubuntu worker.

## Evaluate agents

The Harbor integration converts the first three scenarios into agent tasks. Run
the full Claux matrix with three attempts per scenario:

```nu
nu integrations/harbor/run-isolated-matrix.nu \
  --all-scenarios --agent claux --attempts 3
```

Each attempt gets a fresh NixOS VM, an isolated Docker network, and its own
result directory. The verifier checks the user-facing health path before and
after restarting the repaired service, so an in-memory workaround does not
count as a success. Use `--agent codex` or `--agent claude` to compare other
adapters, or omit `--agent` to run all configured agents.

As one real smoke run, Claux completed 7 of 9 trials across the three scenarios
at a total reported cost of $0.06. The two misses were informative: one repair
did not survive a restart, and one trial timed out. Those are agent-behavior
signals, not setup failures. See [`integrations/harbor/README.md`](integrations/harbor/README.md)
for task definitions, worker setup, and result inspection.

For a deeper dive into why the restart check matters, see [Evaluating
Infrastructure Agents in Running Systems](https://jakegoldsborough.com/blog/2026/evaluating-infrastructure-agents-in-running-systems/).

## Usage

```bash
# add a scenario pack from GitHub
replaybook add ducks/replaybook-scenarios
replaybook add mycompany/incidents

# list available scenarios
replaybook list

# create a runnable scenario in the current pack
replaybook new 010-checkout-down

# validate, test, and run by ID or direct path
replaybook validate ./010-checkout-down
replaybook test ./010-checkout-down
replaybook run ./010-checkout-down

# run an installed scenario (15 minute SLA by default)
replaybook run 001-nginx-502

# run with a custom SLA
replaybook run 001-nginx-502 --sla 5

# run a random scenario, optionally narrowed by tag
replaybook run --random
replaybook run --random --tag postgres

# force a specific fault variant of a multi-fault scenario
replaybook run 006-sidekiq-cant-connect --fault redis-auth

# test a scenario end-to-end without playing it:
# break -> assert broken -> solve -> assert solved
# (multi-fault scenarios get one full cycle per fault)
replaybook test 001-nginx-502
replaybook test 006-sidekiq-cant-connect --fault redis-auth

# test every scenario in a pack (use this in pack CI)
replaybook test --all ./company-incidents

# export session history as JSONL
replaybook export
```

## The HUD

When a scenario starts, the terminal splits via tmux (installed automatically
inside the workstation container - no host dependency). The right pane shows
the incident page, SLA countdown, and hint status.

Run `get-hint` inside the shell to reveal the next hint. Hints used are
recorded with your session outcome.

## Scenario packs

Scenarios live in separate repos and are cloned into
`~/.local/share/replaybook/scenarios/` via `replaybook add`.

Official pack: [ducks/replaybook-scenarios](https://github.com/ducks/replaybook-scenarios)

| ID | Title | Difficulty |
|----|-------|------------|
| 001-nginx-502 | 502 Bad Gateway | 1 |
| 002-postgres-rejecting-connections | Postgres Rejecting Connections | 2 |
| 003-missing-env-var | App Crashing on Boot | 1 |
| 004-disk-full | Health Checks Failing | 2 |
| 005-oom-kill | App Keeps Dying | 2 |
| 006-sidekiq-cant-connect | Jobs Not Processing | 2 |
| 007-packet-loss | Intermittent Request Failures | 3 |
| 008-connection-pool-exhaustion | Checkout Is Down | 3 |
| 009-phantom-backend | Backend Not Receiving Traffic | 3 |

## Writing scenarios

Start with the interactive authoring command:

```bash
replaybook new checkout-db-exhaustion --pack ./company-incidents
replaybook validate ./company-incidents/checkout-db-exhaustion
replaybook test ./company-incidents/checkout-db-exhaustion
```

`new` asks for the incident page, difficulty, tags, hints, learning objectives,
fault and repair commands, success check, and optional incident provenance. It writes a small working
scenario that can be validated and tested immediately; replace the starter
service and fault with the sanitized system behavior from the real incident.

Each scenario is a directory with:

```
my-scenario/
  meta.json            # id, title, page text, difficulty, hints, success condition
  docker-compose.yml   # the environment
  break.sh             # runs after compose up to inject the fault (or use break: [...] below)
  check.sh             # polled every 2s to detect resolution (or use http_200)
  solve.sh             # scripted fix used by `replaybook test` - never shown to players
```

`meta.json` format:

```json
{
  "id": "my-scenario",
  "title": "Something Is Broken",
  "page": "alert text shown to the player",
  "difficulty": 2,
  "hints": [
    "First hint revealed on first get-hint",
    "Second hint revealed on second get-hint"
  ],
  "learning_objectives": [
    "Recognize connection-pool exhaustion",
    "Identify the process consuming connections"
  ],
  "source": {
    "incident_date": "2026-06-14",
    "reference": "INC-1842",
    "sanitized": true
  },
  "success_condition": "http_200",
  "success_target": "http://localhost:8080/health"
}
```

The player always works from the workstation container, which replaybook
attaches to every network the compose file defines. Design faults so the fix
happens inside the apps and services (configs, logs, credentials), not at the
Docker level - and keep faulty containers alive: if the broken process dies
on boot, wrap it in a small supervisor loop so the process crash-loops while
the container stays reachable. See any scenario in
[ducks/replaybook-scenarios](https://github.com/ducks/replaybook-scenarios)
for a working example.

### Fault injection: break.sh vs break steps

Most faults are just "copy a file in" and/or "run a command in a container."
Instead of writing `break.sh`, add a `break` array to `meta.json`:

```json
"break": [
  { "cp": { "service": "nginx", "src": "nginx-broken.conf", "dest": "/etc/nginx/nginx.conf" } },
  { "exec": { "service": "nginx", "cmd": ["nginx", "-s", "reload"] } }
]
```

Steps run in order. Three kinds:

- `cp` - copy `src` (a file in the scenario directory) to `dest` inside `service`'s container
- `exec` - run `cmd` inside `service`'s container
- `restart` - restart `service`'s container

```json
"break": [
  { "cp": { "service": "app", "src": "cache-broken.conf", "dest": "/app/cache.conf" } },
  { "restart": { "service": "app" } }
]
```

If `break` is present, it's used instead of `break.sh`. If a fault needs
real script logic (loops, conditionals, piping between commands), write
`break.sh` instead - it still works exactly as before.

### Fault variants: one symptom, several root causes

A scenario can define a `faults` list instead of a single break. Each run
draws one at random - the page stays the same, so a second run of the same
scenario stays a diagnosis instead of becoming memorization:

```json
"faults": [
  { "name": "redis-auth",
    "break": [ { "exec": { "service": "redis", "cmd": ["..."] } } ],
    "hints": ["hint shown for this fault only"],
    "solve": "solve-auth.sh" },
  { "name": "redis-stopped",
    "script": "break-stopped.sh" }
]
```

Per fault: `break` steps or a `script` filename inject it; `hints` fall
back to the scenario-level hints when omitted; `solve` falls back to
`solve.sh`. Players pick blind (`replaybook run <id>`), the drawn fault is
revealed after the run and recorded on the session. `--fault <name>`
forces one; `replaybook test` cycles through all of them.

`replaybook validate <id-or-path>`, `replaybook add`, and `replaybook run`
validate each scenario (compose file
parses, any `break` step's `service` matches a real service, `break.sh` or
`break` exists, and `check.sh` exists if `success_condition` is `exit_zero`)
and report problems before anything runs.

### Testing scenarios

`replaybook test <id-or-path>` verifies a scenario end-to-end without a player: it
brings the stack up, injects the fault, asserts the check fails, runs the
scenario's `solve.sh`, and asserts the check recovers. Run it in CI on your
scenario pack so broken scenarios never reach players:

```bash
replaybook test --all ./company-incidents
```

### A note on trust

Scenario packs are code. `break.sh`, `check.sh`, and `solve.sh` run on
**your machine** with your privileges, and the workstation container gets
the Docker socket. Only add packs you'd be comfortable running as a shell
script - which is exactly what they are.

## Session data

Sessions are recorded to `~/.local/share/replaybook/sessions/sessions.jsonl`:

```bash
replaybook export > sessions.jsonl
```

Each record contains scenario ID, outcome (success/timeout/abandoned),
elapsed time, hints used, and the path to the session transcript.

Every run also records a full terminal transcript of the player's shell
pane (via tmux pipe-pane) to
`~/.local/share/replaybook/sessions/transcripts/<scenario>-<timestamp>.log` -
every command typed and everything it printed. Review it after a run to
compare what you did against the scenario's intended fix, or feed it to
whatever training/analysis pipeline you like. Transcripts are raw terminal
output (ANSI escapes included); `less -R` renders them nicely.

## Releasing

```bash
make release
```

Bumps version with today's date, tags, pushes, publishes to crates.io.
GitHub Actions builds binaries for all platforms on the tag push.
