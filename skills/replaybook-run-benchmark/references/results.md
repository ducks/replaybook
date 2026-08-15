# Result Interpretation and Publication

## Artifact Routing

Start with matrix `summary.json`, then use the narrowest raw artifact:

- `logs/<run-id>.log`: controller, VM startup, adapter, and verification flow.
- `runs/<run-id>/result.json`: normalized status, failure category, usage,
  verification phases, versions, and post-timeout outcome.
- Transcript path named by `result.json`: harness-native model rounds and tool
  activity when recorded.
- `benchmark.json`: planned dimensions, timeout, and execution identity.
- `execution-snapshot/manifest.json`: hashes of the frozen controller, packs,
  adapter, payload, and benchmark manifest.

Use `jq` to inspect structure before assuming a field exists:

```sh
jq '{totals, failure_categories, unavailable_categories}' MATRIX/summary.json
jq '{trial_status, passed, failure_category, verification, usage}' \
  MATRIX/runs/RUN/result.json
```

## Status Boundaries

`evaluated` means an agent received a meaningful opportunity and the external
verifier scored the resulting host. A failed evaluated trial belongs in the
pass-rate denominator.

`unavailable` means provider, harness, authentication, or known guest-boot
infrastructure prevented meaningful evaluation. Report and investigate it,
but exclude it from the model pass rate.

A worker producing no valid result makes the matrix incomplete. It is not
automatically a model failure or unavailable model trial. Read its worker log.

Failure categories describe observable outcomes. Do not infer model intent
from names such as `backlog_not_recovered`, `release_not_converged`, or
`host_reboot_failed`. Establish what the verifier observed before reading the
transcript for an explanation.

## Cost and Timing

Report known spend, evaluated attempts, durable passes, median duration, and
price per durable repair together. Include failed evaluated attempts in total
spend. Keep subscription-backed or otherwise unmetered cost unavailable, not
zero. A trailing `+` or incomplete usage coverage means known cost is a lower
bound.

Execution recording separates model time from tool time and records rounds,
tool calls, time to first write-capable action, and time remaining afterward.
Use it to distinguish slow inference, tool stalls, late action, and reasoning
loops. Missing recording is an adapter capability difference, not zero work.

## Local Catalog

```sh
python -m integrations.host.result_catalog import jobs
python -m integrations.host.result_catalog compare --scenario SCENARIO
```

When multiple compatibility cohorts exist, list them and explicitly choose the
one being analyzed. Do not merge cohorts to make a cleaner table.

## Public Release

Import reviewed compatible summaries into a new DateVer snapshot:

```sh
python integrations/host/publish_benchmarks.py import \
  --version YYYYMMDD.0.0 \
  --annotations benchmark-data/annotations/YYYYMMDD.0.0.json \
  MATRIX-A/summary.json MATRIX-B/summary.json
python integrations/host/publish_benchmarks.py build
```

Use annotations for transparent editorial context or a documented correction,
not to silently alter raw outcomes. Review the public catalog for local paths,
credentials, transcripts, and unpublished details before committing.
