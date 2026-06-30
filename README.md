# replaybook

Incident replay trainer. Get paged, fix real broken infrastructure, win.

Each scenario is a Docker environment with a fault injected. You're dropped
into a shell inside the broken container. The terminal splits - shell on the
left, HUD on the right showing the incident page, SLA countdown, and hints.
Diagnose and fix using real tools. The engine polls a health check in the
background - when it goes green, you're done.

Built to turn post-mortems into playable scenarios. New engineers build muscle
memory on the actual failure modes your team has hit, not simulations.

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
replaybook add ducks/on-call-scenarios

# see what's available
replaybook list

# run your first scenario
replaybook run 001-nginx-502
```

Both `replaybook` and `replay` are installed - use whichever you prefer.

## Usage

```bash
# add a scenario pack from GitHub
replaybook add ducks/on-call-scenarios
replaybook add mycompany/incidents

# list available scenarios
replaybook list

# run a scenario (15 minute SLA by default)
replaybook run 001-nginx-502

# run with a custom SLA
replaybook run 001-nginx-502 --sla 5

# export session history as JSONL
replaybook export
```

## The HUD

When a scenario starts, the terminal splits via tmux (installed automatically
inside the container - no host dependency). The right pane shows the incident
page, SLA countdown, and hint status.

Run `get-hint` inside the shell to reveal the next hint. Hints used are
recorded with your session outcome.

## Scenario packs

Scenarios live in separate repos and are cloned into
`~/.local/share/replaybook/scenarios/` via `replaybook add`.

Official pack: [ducks/on-call-scenarios](https://github.com/ducks/on-call-scenarios)

| ID | Title | Difficulty |
|----|-------|------------|
| 001-nginx-502 | 502 Bad Gateway | 1 |
| 002-postgres-wont-start | Postgres Won't Start | 1 |
| 003-missing-env-var | App Crashing on Boot | 1 |
| 004-disk-full | Health Checks Failing | 2 |
| 005-oom-kill | Container Keeps Restarting | 2 |
| 006-sidekiq-cant-connect | Jobs Not Processing | 2 |
| 007-packet-loss | Intermittent Request Failures | 3 |

## Writing scenarios

Each scenario is a directory with:

```
my-scenario/
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
  "hints": [
    "First hint revealed on first get-hint",
    "Second hint revealed on second get-hint"
  ],
  "success_condition": "http_200",
  "success_target": "http://localhost:8080/health",
  "shell_service": "app"
}
```

`shell_service` is the compose service the player is dropped into. Defaults
to `app`. See any scenario in
[ducks/on-call-scenarios](https://github.com/ducks/on-call-scenarios) for a
working example.

## Session data

Sessions are recorded to `~/.local/share/replaybook/sessions/sessions.jsonl`:

```bash
replaybook export > sessions.jsonl
```

Each record contains scenario ID, outcome (success/timeout/abandoned),
elapsed time, and hints used.

## Releasing

```bash
make release
```

Bumps version with today's date, tags, pushes, publishes to crates.io.
GitHub Actions builds binaries for all platforms on the tag push.
