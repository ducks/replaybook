# Harbor evaluation spike

This directory contains a proof of concept for running a Replaybook incident as
a graded [Harbor](https://harborframework.com/) task. It deliberately lives
outside Replaybook's CLI while the integration boundary is being evaluated.

The spike converts `001-nginx-502` into a deterministic task. Harbor runs the
agent in a `main` workstation container alongside the incident's application
and nginx containers. The workstation mounts the local Docker socket, matching
Replaybook's current workstation security model, so the agent can inspect and
repair the sibling services with `docker ps`, `docker logs`, `docker exec`, and
`docker cp`.

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
permissions, and saves its console output under Harbor's agent logs.

## Run the three-agent comparison

After the individual smoke tests pass, run three attempts each for Codex,
Claude Code, and Claux with:

```sh
claude setup-token
nu integrations/harbor/run-three-agent-smoke.nu
```

The launcher reads `CLAUDE_CODE_OAUTH_TOKEN` and `OPENROUTER_API_KEY` from the
environment when set, otherwise prompts for them privately. It selects a
compatible Nix-provided Buildx without modifying the user Docker installation
and starts the job defined in
`jobs/three-agent-smoke.yaml`. The job runs nine trials total, with up to one
trial per agent and three trials overall executing concurrently.

The fixed pairings are:

- Codex with `gpt-5.6-sol`
- Claude Code with `claude-sonnet-5`
- Claux with `deepseek/deepseek-v4-flash` through OpenRouter

## What this proves

- Harbor can represent Replaybook's multi-container workstation topology.
- A fault can be selected deterministically by baking its broken state into a
  task variant.
- Replaybook's objective recovery check maps to a Harbor verifier reward.
- Harbor can run a reference solution without exposing it to a normal agent.

## Known limitations

- This task is a hand-built conversion, not an exporter.
- The Docker socket makes the agent root-equivalent on the Docker host. Run it
  only on a disposable or trusted machine.
- The verifier shares the agent environment. This is sufficient for an honest
  harness comparison, but a public adversarial benchmark needs a controller-
  held verifier that the worker cannot inspect or modify.
- The Claux adapter records console output but does not yet translate Claux's
  structured session into an ATIF trajectory or report tokens and cost to
  Harbor.

If the oracle and a real agent both run successfully, the next step is to
generate one Harbor task per Replaybook scenario and fault variant rather than
maintaining copied task definitions by hand.
