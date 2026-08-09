# Scenario design reference

Host scenarios may live in Replaybook's bundled pack or an external pack. An
external pack root must include `replaybook-pack.toml` with a stable `pack.id`
and version. Increment the pack version whenever scenario contents or verifier
behavior change, even when individual scenario version numbers are unchanged.

## Incident contract

Define these fields in notes before writing code:

| Field | Question |
| --- | --- |
| Symptom | What does a user or operator observe? |
| Root cause | What single deployed defect causes it? |
| Durable repair | What persistent change should survive restart and reboot? |
| Existing state | Which exact jobs, records, or requests must survive? |
| Future state | What new work must succeed after the repair? |
| Topology | Which services and dependencies must remain present? |
| Negative controls | Which shortcuts must fail verification? |

Prefer incidents where a green process or shallow health check coexists with a
broken user path. This forces investigation across service boundaries.

## Stateful verification

Traditional code checks validate an artifact. Replaybook validates the running
system after state transitions. A strong verifier checks all relevant layers:

1. The incident exists before the agent starts.
2. Exact controller-owned work created before repair is recovered.
3. New work succeeds after repair.
4. Required services and topology remain intact.
5. The same checks pass after selected services restart.
6. The same checks pass after the host reboots.

Use opaque, generated identifiers. Persist them only in controller state. The
agent may discover the affected records through normal investigation, but
should not receive the verifier's expected set.

For a queue incident, requiring only an empty queue is unsafe: deletion passes.
Require completion records for the exact queued identifiers. For a schema
incident, requiring only a column is unsafe: an agent may add it without the
deployment's migration record. Verify both when both are operationally
meaningful.

## Declarative phase selection

Use `wait_http` for bounded readiness and stable HTTP assertions. Use
`concurrent_http` to create load or controller-owned work. Use `replay_http` to
check or retry exact IDs stored by an earlier step. Add `timeout_seconds` when
each ID may need bounded polling before it reaches the expected state. Use
`initial_delay_seconds` when the incident contract requires observing that work
remains blocked for a minimum period. Keep request deadlines short and phase
deadlines bounded.

Use a legacy `preflight.sh` or `verify.sh` only for assertions the phase runner
cannot express, such as direct database invariants or service topology. Keep
those scripts controller-side. Do not copy them into the model VM.

Failure categories should state the failed system invariant, such as
`backlog_not_recovered`, `migration_not_applied`, or
`database_pool_exhausted`. Avoid categories that speculate about reasoning,
such as `agent_misunderstood_redis`.

## Leak audit

Inspect every file available inside the incident VM, including Nix derivation
names, service descriptions, environment filenames, source comments, seeded
data, logs, migration names, shell history, and process arguments.

Reject leaks that expose:

- the exact bad value and replacement value;
- words such as `broken`, `wrong`, `oracle`, `verifier`, or `expected` near the
  fault;
- evaluation-only paths or controller state;
- the repair command in comments or logs;
- a service or file named after the root cause.

Realistic operational clues are allowed. A connection-refused log containing
the configured upstream port is evidence, not a leak. A comment saying
`intentionally wrong port for benchmark` is a leak.

## Adversarial verifier review

Before running models, attempt these shortcuts mentally or with a local copy:

- start a second process manually;
- redirect traffic with firewall rules;
- disable or remove a required service;
- delete the backlog or failing records;
- hard-code the verifier's known identifier;
- return a constant success response;
- repair only in memory;
- change the public topology;
- pass immediate verification but fail after restart;
- pass restart verification but fail after reboot.

Add an external assertion for any shortcut that currently passes.

## Acceptance sequence

1. Validate static structure with `validate_scenario.py`.
2. Build and run the scenario oracle.
3. Confirm all three verification phases pass.
4. Run one cheap model smoke test.
5. Inspect its transcript for leaks, ambiguity, and verifier gaps.
6. Run at least three attempts per compared model before reporting rates.
7. Record scenario and host-harness versions with results.
