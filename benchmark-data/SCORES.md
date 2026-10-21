# Core scores

The public Scores page uses the shared benchmark layout. The homepage and
benchmark navigation link to it; Evidence remains the detailed attempt view.

`scores.json` explicitly selects releases and frozen Core manifests. Add a
reviewed cohort here, not a model ID in application code. Never choose the best
run automatically or pool releases. Each selection includes benchmark, harness,
and release identity; provider sections contain separate model/reasoning lanes.

The default view groups releases with matching benchmark conditions, not matching
agent harnesses. A conditions fingerprint includes the manifest, suite, attempts,
deadline, controller versions and hash, and selected scenario content hashes.
Missing controller or scenario provenance isolates a release. Different
controllers or scenario content remain separate even if version labels match.
Agent harness, payload, adapter, provider, reasoning, and model are experimental
variables, not grouping requirements. Every card retains its harness/version and
release, and a harness filter narrows the view. Comparing two cards that change
both provider and harness cannot isolate the effect of either one.

Manifest copies are byte-for-byte snapshots, including their hashes. They are
score contracts, not runnable scenario packs in this repository. Historical
snapshots must not be edited when the source pack changes.

Score = 100 × mean(per-scenario passed / recorded attempts). Unavailable attempts
count against the headline. Conditional repair diagnostics exclude unavailable
trials, so the Evidence percentage can differ. Cost and duration are available
through the linked evidence and are not part of the score.

Cards require exact contract identity, manifest hash, host harness version,
deadline, attempt count, and every required scenario/version. Partial coverage
has no public headline number. Three attempts per scenario remain a small
sample; completeness does not establish statistical certainty.

Regenerate and verify with:

```
python integrations/host/publish_benchmarks.py build
python integrations/host/publish_benchmarks.py check
make pages-check
python -m unittest integrations.host.test_benchmark_scores
```

The original local score spike remains separate and is not imported or published.
