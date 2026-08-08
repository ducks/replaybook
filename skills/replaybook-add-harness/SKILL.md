---
name: replaybook-add-harness
description: Build, test, and document a harness adapter for Replaybook host-native infrastructure evaluations. Use when integrating a coding or operations agent CLI with Replaybook, creating an adapter for a new harness, normalizing harness results and transcripts, staging a harness binary or credentials into the disposable VM, or debugging a custom harness that fails before evaluation.
---

# Add a Replaybook Harness

Integrate a harness without coupling Replaybook's scenario lifecycle or
verifier to that harness. Keep the adapter narrow: translate Replaybook's
environment contract into one CLI invocation, then normalize the result.

## Workflow

1. Locate the Replaybook repository and read the current contract files:
   `integrations/host/run-agent-adapter.sh`, `run-host-native.sh`, and
   `run_host_matrix.py`. Read [references/contract.md](references/contract.md)
   for the stable concepts, but prefer repository code when versions differ.
2. Inspect the harness's installed CLI and noninteractive help. Confirm:
   invocation, model selection, working-directory control, unrestricted mode,
   machine-readable events, final-message output, cancellation behavior, and
   authentication choices.
3. Choose the smallest staging plan:
   - `--agent-adapter`: required adapter script.
   - `--agent-payload`: optional native binary or harness artifact.
   - `--agent-env-file`: optional mode-0600 shell assignments.
   Never stage a complete home directory when one binary and one scoped
   credential suffice.
4. Copy [assets/adapter.sh](assets/adapter.sh) into
   `integrations/host/adapters/<harness>.sh`. Replace every `TODO` and remove
   unused paths. Preserve signal forwarding and partial-result capture.
5. Normalize output to `REPLAYBOOK_RESULT_FILE`. Validate it with:

   ```sh
   python skills/replaybook-add-harness/scripts/validate_result.py \
     RESULT.json --harness HARNESS --model MODEL
   ```

6. Add a fake-CLI smoke test to `integrations/host/test-host-native.sh`. Test
   arguments, prompt delivery, credentials, final response, usage mapping,
   transcript capture, and nonzero exit handling without calling a model.
7. Run the repository's full lint command. Then run one real attempt on
   `001-nginx-502-host`. Do not start a larger or paid matrix until that trial
   produces a normalized result and passes restart and reboot verification.
8. Document the exact Nushell-friendly setup and run commands in
   `integrations/host/README.md`. State whether cost is metered, calculated,
   reported by the harness, or unavailable.

## Design Rules

- Treat the VM as the security boundary. Grant the agent the access needed to
  repair the host, but do not weaken the controller boundary.
- Never copy a scenario oracle, verifier, preflight implementation, or
  controller state into the agent directory.
- Prefer API keys or automation tokens scoped to one run. If reusing a login
  cache, remove refresh credentials unless refreshed state is safely returned
  to its owner.
- Run the harness in an isolated home/config directory. Ignore personal hooks,
  plugins, MCP servers, and rules unless they are deliberately part of the
  harness under evaluation.
- Forward `TERM` and `INT` to the child. Preserve any valid partial transcript
  and usage before exiting with the child's status.
- Keep native transcripts harness-specific. Only the normalized result schema
  belongs to Replaybook.
- Do not infer monetary cost from tokens unless the benchmark records a
  versioned pricing source. Use `cost_usd: null` for subscriptions or unknown
  pricing.
- A harness startup or authentication failure is not evidence about model
  capability. Diagnose it from the worker log before interpreting the score.

## Completion Checklist

- Adapter contains no secrets or machine-specific paths.
- Payload locator resolves the actual executable, not a package-manager shim.
- Credential helper emits mode 0600 and excludes unnecessary long-lived data.
- Fake CLI test passes without network access.
- Normalized result validator passes.
- `make lint` passes.
- One real scenario passes immediate, service-restart, and host-reboot checks.
- Temporary credential files are removed and the feature branch is clean.
