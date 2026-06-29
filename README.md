# on-call

A terminal game where you get paged and have to fix real broken infrastructure to win. Each round is an incident. The environment is a real Docker container. The tools are yours.

Built as a training tool - turn post-mortems into playable scenarios. New engineers build muscle memory on the actual failure modes your team has hit, not simulations.

## How it works

1. Run a scenario
2. A broken environment spins up in Docker
3. You're dropped into a shell inside the broken container
4. The terminal splits - shell on the left, HUD on the right (incident page, SLA countdown, hints)
5. Diagnose and fix using real tools (`nginx -t`, `psql`, `tc qdisc`, whatever applies)
6. The engine polls a health check in the background - when it goes green, you win

## Getting started

```bash
cargo install on-call
```

Requires Docker. Then:

```bash
# see what's available
on-call list

# run your first scenario
on-call run 001-nginx-502
```

The terminal splits - shell inside the broken container on the left, HUD on the right. Fix the problem before the SLA runs out.

## Install

```bash
cargo install on-call
```

Prebuilt binaries for linux-x86_64, linux-arm64, macos-x86_64, macos-arm64 are available on the [releases page](https://github.com/ducks/on-call/releases).

## Usage

```bash
# list available scenarios
on-call list

# run a scenario (15 minute SLA by default)
on-call run 001-nginx-502

# run with a custom SLA
on-call run 001-nginx-502 --sla 5

# export session history as JSONL
on-call export
```

## The HUD

When a scenario starts, the terminal splits via tmux (installed automatically inside the container - no host dependency). The right pane shows the incident page, SLA countdown, and hint status.

Run `get-hint` inside the shell to reveal the next hint. Hints used are recorded with your session outcome.

## Scenarios

| ID | Title | Difficulty |
|----|-------|------------|
| 001-nginx-502 | 502 Bad Gateway | 1 |
| 002-postgres-wont-start | Postgres Won't Start | 1 |
| 003-missing-env-var | App Crashing on Boot | 1 |
| 004-disk-full | Health Checks Failing | 2 |
| 005-oom-kill | Container Keeps Restarting | 2 |
| 006-sidekiq-cant-connect | Jobs Not Processing | 2 |
| 007-packet-loss | Intermittent Request Failures | 3 |

## Adding scenarios

Each scenario is a directory under `scenarios/` with:

```
scenarios/my-scenario/
  meta.json            # id, title, page text, difficulty, hints, success condition
  docker-compose.yml   # the environment
  break.sh             # runs after compose up to inject the fault
  check.sh             # polled every 2s to detect resolution (or use http_200)
```

`meta.json` format:

```json
{
  "id": "my-scenario",
  "title": "Something Is Broken",
  "page": "alert text shown to the player",
  "difficulty": 2,
  "tags": ["nginx", "networking"],
  "hints": [
    "First hint revealed on first get-hint",
    "Second hint revealed on second get-hint"
  ],
  "success_condition": "http_200",
  "success_target": "http://localhost:8080/health",
  "shell_service": "app"
}
```

`shell_service` is the compose service the player is dropped into. Defaults to `app`.

See `scenarios/001-nginx-502/` for a working example.

## Releasing

```bash
make release
```

Bumps version with today's date, tags, pushes, publishes to crates.io. GitHub Actions builds binaries for all platforms on the tag push.

## Session data

Sessions are recorded to `~/.local/share/on-call/sessions/sessions.jsonl`:

```bash
on-call export > sessions.jsonl
```

Each record contains scenario ID, outcome (success/timeout/abandoned), elapsed time, and hints used.
