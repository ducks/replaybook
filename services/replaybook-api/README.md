# Hosted Replaybook API

This service is Replaybook's hosted control plane. It accepts scoped scenario
runs, assigns them to warm workers, records lifecycle events, and returns
normalized verification results. The API is intentionally separate from the
local Replaybook CLI and verifier boundary.

## Binaries

- `replaybook-api` — HTTP control plane
- `replaybook-worker` — worker that polls for assignments and invokes a
  worker-local executor

The first implementation was prototyped in the `agent-zen-garden` repository;
this directory is now the source of truth. The old repository can remain as a
historical pointer until the hosted API is deployed from Replaybook.

## Local smoke test

```sh
AGENT_ZEN_GARDEN_JOIN_TOKEN=dev-token \
AGENT_ZEN_GARDEN_STATE_FILE=/tmp/replaybook-api.db \
cargo run --bin replaybook-api
```

The API includes a built-in scenario catalog and exposes it at
`GET /v1/scenarios`. Set `AGENT_ZEN_GARDEN_SCENARIOS_FILE` to a JSON catalog to
override it for a deployment.

## Deployment

The systemd units and environment templates in this directory target a
control-plane host and a separate warm worker. Keep the join token in a
mode-0600 environment file; never put it in a URL or run payload. The worker
executor path is fixed on the worker and is not supplied by requesters.

The current control plane uses SQLite for single-process durable state. Before
opening public registration, add the remaining API-contract work: run
idempotency and expiry, worker leases, protocol negotiation, artifact storage,
and an authenticated operator surface.
