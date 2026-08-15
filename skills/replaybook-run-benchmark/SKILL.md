---
name: replaybook-run-benchmark
description: Plan, validate, run, resume, inspect, compare, and publish reproducible Replaybook host-native infrastructure-agent benchmarks. Use when choosing scenarios, models, reasoning efforts, attempts, timeouts, concurrency, or harnesses; constructing Bash or Nushell-safe matrix commands; diagnosing unavailable, infrastructure, timeout, or verifier failures; examining result and transcript artifacts; calculating price per durable repair; or updating Replaybook benchmark releases without mixing incompatible evidence.
---

# Run a Replaybook Benchmark

Run controlled experiments against disposable hosts. Keep scenario truth,
harness behavior, provider availability, and model performance separate.

## Workflow

1. Locate the Replaybook repository. Read the current
   `integrations/host/run_host_matrix.py --help` and relevant sections of
   `integrations/host/README.md`; prefer them over old notes or conversations.
2. Identify the experiment before spending tokens:
   - Prefer `--benchmark PATH/benchmark.toml` for a published comparison.
   - Otherwise list scenarios with `--list-scenarios` and repeat `--scenario`
     for every selected incident.
   - Record models, reasoning efforts, attempts, timeout, adapter, and intended
     comparison cohort.
3. Check the host:

   ```sh
   python skills/replaybook-run-benchmark/scripts/check_host.py --repo .
   ```

   Resolve failed prerequisites and severe disk or memory pressure first.
   Treat the concurrency estimate as a conservative starting point, not a
   guarantee. Stateful Ruby and Discourse scenarios may need more headroom.
4. Validate before running agents:

   ```sh
   python integrations/host/run_host_matrix.py \
     --benchmark ../replaybook-infra/benchmark.toml \
     --check
   ```

   For a new or changed scenario, run the benchmark with `--oracle`. Never
   interpret model failures until the controller-owned repair passes immediate,
   service-restart, and host-reboot verification.
5. Start with one inexpensive smoke trial. Proceed only after it returns a
   normalized result. Obtain approval before launching a materially larger or
   expensive matrix.
6. Construct the measured run. Use at least three attempts for an initial
   reliability estimate. Keep one matrix to one harness and compatible
   benchmark definition. Do not tune the prompt, timeout, or verifier between
   models in the same comparison.
7. Monitor progress without modifying the frozen execution snapshot. A feature
   branch or live source edit does not change already-scheduled workers.
8. If interrupted, resume the existing directory instead of starting over:

   ```sh
   python integrations/host/run_host_matrix.py \
     --resume jobs/host-matrix-TIMESTAMP.ID \
     --concurrency 2
   ```

   Add `--refresh-controller` only when pending trials require a controller
   fix. Replaybook retains completed results and the frozen scenario pack.
9. Interpret `summary.json` and raw artifacts using
   [references/results.md](references/results.md). Inspect worker logs before
   labeling an uncategorized or unavailable result as model behavior.
10. Import local results into the catalog for exploration. Publish only an
    explicitly selected, compatible cohort. Rebuild generated pages and run
    repository lint before committing.

## Command Construction

Prefer an executable benchmark manifest:

```sh
python integrations/host/run_host_matrix.py \
  --benchmark ../replaybook-infra/benchmark.toml \
  --models deepseek/deepseek-v4-flash-0731 openai/gpt-5.6-luna \
  --reasoning-efforts high \
  --concurrency 2
```

For direct selection, repeat `--scenario`; there is no implicit
`--all-scenarios` flag. Supplying a scenario pack changes the available set but
does not select every scenario in it.

For Nushell, use a list of literal arguments and spread it into an external
command. This avoids line-continuation and `name=value` parse errors:

```nu
let matrix_args = [
  "integrations/host/run_host_matrix.py"
  "--benchmark" "../replaybook-infra/benchmark.toml"
  "--models" "deepseek/deepseek-v4-flash-0731" "openai/gpt-5.6-luna"
  "--reasoning-efforts" "high"
  "--concurrency" "2"
]
^python ...$matrix_args
```

Keep adapter assignments such as `claux=adapters/claux.sh` inside the argument
list as quoted strings. Do not translate Bash backslash continuations into
Nushell source mechanically.

## Evaluation Rules

- Treat the external verifier as authoritative. The agent's diagnosis or
  success claim is not a score.
- Keep evaluated failures, unavailable trials, and missing/infrastructure
  results distinct. Never turn provider denial into a failed repair.
- A repair completed after the deadline remains an `agent_timeout` failure,
  even when post-timeout verification finds durable state.
- Calculate price per durable repair from total known spend divided by durable
  passes. Failed evaluated attempts consume spend. Mark unknown or partial
  spend rather than treating it as zero.
- Compare only matching scenario versions, pack versions, benchmark manifests,
  harness snapshots, adapters, timeout policy, and relevant agent releases.
- Do not publish secrets, local paths, raw transcripts, VM logs, or unreviewed
  annotations.

## Completion Checklist

- Host preflight has no blocking failures.
- Benchmark manifest check and oracle pass when applicable.
- Smoke trial produces a valid normalized result.
- Matrix dimensions and estimated maximum spend are stated before launch.
- Interrupted work is resumed rather than duplicated.
- Failures are classified from artifacts, not guessed from a summary table.
- Published inputs form one compatible cohort.
- Generated benchmark files are current and `make lint` passes.
- Feature branch contains no `jobs/`, credentials, or private artifacts.
