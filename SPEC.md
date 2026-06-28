# on-call

A terminal game where you get paged and have to fix real broken infrastructure
to win. You are the on-call engineer. The systems are real Docker containers.
The breakage is real. The tools are real.

Inspired by BOFH. Built as a training tool.

## The premise

Each round starts with a page: a fake PagerDuty alert, a Slack message from a
panicking coworker, a terse ticket. Something is broken in production. You have
a terminal. Fix it before the SLA clock runs out.

You're not clicking UI buttons. You're running `docker logs`, `curl`,
`psql`, `journalctl`, `strace` - whatever you need. The game watches for a
success condition (health check green, service responding, migration complete)
and advances when you hit it.

## Architecture

```
on-call/
  engine/         # Go or Rust CLI - spins up scenarios, watches success conditions
  scenarios/      # One directory per incident, self-contained
  runner/         # Docker Compose wrapper, injects breakage, exposes shell
  recorder/       # Captures command sequences for training data export
```

Each scenario is a self-contained directory:

```
scenarios/
  001-nginx-502/
    docker-compose.yml   # the broken environment
    break.sh             # applied at start to inject the fault
    check.sh             # polled to detect success
    meta.json            # title, description, difficulty, hints, tags
  002-postgres-wont-start/
    ...
```

## Scenario format

`meta.json`:
```json
{
  "id": "001-nginx-502",
  "title": "502 Bad Gateway",
  "page": "URGENT: users getting 502s on checkout. conversion is tanking. fix asap",
  "difficulty": 1,
  "tags": ["nginx", "proxy", "networking"],
  "hints": [
    "Check what nginx is proxying to",
    "Is the upstream actually running?"
  ],
  "success_condition": "http_200",
  "success_target": "http://localhost:8080/health"
}
```

`break.sh` - injected after `docker compose up`, before the player gets the
terminal. Examples:
- Wrong upstream port in nginx.conf
- App container missing an env var so it crashes on boot
- Postgres data directory with wrong permissions
- Memory limit set too low, OOM killer firing
- DNS misconfiguration inside the container network
- Bad migration left the DB in a half-applied state
- SSL cert expired
- Disk full (fallocate a big file)
- Clock skew breaking JWT validation

`check.sh` - polled every 5 seconds:
```bash
#!/bin/bash
curl -sf http://localhost:8080/health && exit 0
exit 1
```

## Game loop

1. Engine picks next scenario (sequential or random)
2. `docker compose up` in scenario dir
3. `break.sh` runs to inject fault
4. Player gets terminal + the page text + SLA timer
5. Engine polls `check.sh` every 5s
6. On success: score (time taken, hints used), advance
7. On timeout: show what was broken, option to retry

## Difficulty progression

- **Day 1-3**: Single obvious fault. nginx pointing at wrong port. Missing env
  var that's right there in the error log.
- **Day 4-7**: Two faults. Fix the obvious one, find the subtle one.
- **Day 8+**: Cascading failures. The app is crashing because the DB is slow
  because the disk is full because a log rotation cron never ran.
- **Endgame**: No error messages. Silent failures. Wrong behavior, no
  exception. You have to reason from metrics.

## Training data

Every session is recorded:

```json
{
  "scenario": "001-nginx-502",
  "broken_state": { "nginx_upstream_port": 3001, "app_port": 3000 },
  "commands": [
    { "t": 0, "cmd": "docker ps" },
    { "t": 4, "cmd": "docker logs on-call-nginx-1" },
    { "t": 12, "cmd": "docker exec on-call-nginx-1 cat /etc/nginx/nginx.conf" },
    { "t": 23, "cmd": "docker exec on-call-nginx-1 sed -i 's/3001/3000/' /etc/nginx/nginx.conf" },
    { "t": 24, "cmd": "docker exec on-call-nginx-1 nginx -s reload" }
  ],
  "resolved_at": 24,
  "hints_used": 0,
  "outcome": "success"
}
```

Enough sessions = labeled dataset of:
- broken infra state
- diagnostic command sequences
- resolution steps

An agent trained on this learns to on-call. The game generates the data as a
side effect of being fun.

## Stack

- Engine: Go CLI (fast startup, good Docker SDK support)
- Scenarios: Docker Compose + bash
- Terminal UI: Bubble Tea (Go TUI library) or just raw terminal
- Recorder: writes JSONL per session to `~/.on-call/sessions/`
- Export: `on-call export --format jsonl` dumps training data

## Scenarios to build first

1. `nginx-502` - upstream port mismatch
2. `postgres-wont-start` - data dir permissions
3. `missing-env-var` - app crashes silently on boot
4. `disk-full` - write failures, cryptic errors
5. `bad-migration` - DB half-migrated, app stuck in error loop
6. `oom-kill` - memory limit too low, container keeps restarting
7. `cert-expired` - SSL handshake failures
8. `clock-skew` - JWT validation failing, no obvious reason why
9. `dns-misconfigured` - service can't reach dependency by name
10. `log-rotation-never-ran` - cascading disk full from accumulated logs

## Name

Repo: `on-call`. Tone: BOFH. The page texts should be written like a real
panicking Slack message. The hints should be slightly passive-aggressive. The
success message should be dry.
