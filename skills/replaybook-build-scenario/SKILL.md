---
name: replaybook-build-scenario
description: Design, implement, validate, and test a Replaybook host-native infrastructure incident scenario. Use when adding a new benchmark incident, converting an operational failure into a disposable NixOS scenario, writing a durable external verifier and oracle repair, preserving pre-existing work across repair, assigning failure categories, or auditing a scenario for leaks and false-positive passes.
---

# Build a Replaybook Scenario

Create a stateful infrastructure evaluation, not a disguised code puzzle. The
agent must investigate a running host, repair the deployed system, and preserve
the repair through service restart and host reboot.

## Workflow

1. Read `integrations/host/README.md`, `run-host-native.sh`,
   `scenario_phase.py`, and one nearby declarative scenario. Prefer the current
   repository contract over this skill when they differ.
2. Write the incident contract before implementation: user-visible symptom,
   hidden root cause, state that must not be lost, allowed durable repairs,
   forbidden shortcuts, and exact observable conditions for success.
3. Scaffold a new scenario, then add only the application files needed to
   produce the incident:

   ```sh
   python skills/replaybook-build-scenario/scripts/scaffold_scenario.py \
     NNN-name
   ```
4. Build a normal NixOS service topology. Introduce one intentional fault in
   deployed state or configuration. Do not name the fault in unit names,
   filenames, comments, logs, the instruction, or other agent-visible clues.
   Declare answer-shaped labels in `[guest_leak_audit]` so Replaybook checks the
   built guest before starting the agent.
5. Implement preflight first. Prove the healthy control path and the broken
   user path. When existing work matters, generate controller-owned IDs and
   record them for verification.
6. Write the external verifier from the success contract. Verify the
   user-facing boundary, exact pre-existing state, future work, and prohibited
   shortcuts. Replaybook will run it immediately, after restarting configured
   services, and after rebooting the VM.
7. Write the smallest durable oracle repair. The oracle is an answer key, not a
   second verifier. Never make it available to a model trial.
8. Validate the scenario:

   ```sh
   python skills/replaybook-build-scenario/scripts/validate_scenario.py \
     integrations/host/scenarios/NNN-name
   ```

9. Run `make lint`, then prove the scenario with `--oracle`. Run one inexpensive
   model attempt only after the oracle passes immediate, restart, and reboot
   verification. Repeat attempts before publishing benchmark claims.
10. Document the scenario and its version. Increment the scenario version when
    setup, instructions, verification, or scoring changes comparability.

Read [references/design.md](references/design.md) while defining the incident
and verifier. It contains the benchmark invariants, leak audit, and review
questions. Use legacy shell preflight or verification only when the typed phase
runner cannot express an essential assertion.

## Design Rules

- A passing health check is not evidence of a repair.
- Verify outcomes from outside the model VM. Never trust the agent's report.
- Require durable state, not a manually launched replacement process, port
  redirect, disabled dependency, cleared queue, or deleted failing record.
- Preserve controller-owned identifiers for backlog and retry assertions.
- Keep infrastructure failures separate from model failures.
- Give the agent normal host tools and realistic evidence, but no evaluation
  filenames, expected values, failure categories, verifier, or oracle.
- Keep one primary fault per scenario. Incidental complexity may provide
  realistic noise, but must not create multiple unrelated valid diagnoses.
- Make the oracle use the same authority available to the evaluated agent.
- Do not tune the prompt or verifier around one model's transcript.

## Completion Checklist

- Broken state reproduces reliably from a clean VM.
- Oracle passes immediate, service-restart, and host-reboot verification.
- Pre-existing work is recovered, not discarded, when the incident has state.
- A temporary workaround cannot pass the verifier.
- An unrelated topology change cannot pass the verifier.
- Agent-visible files do not disclose the answer or evaluation machinery.
- Failure categories describe verifier outcomes, not guessed model intent.
- Validator and `make lint` pass.
- One real model smoke test produces a valid result.
- Feature branch contains no generated job artifacts or credentials.
