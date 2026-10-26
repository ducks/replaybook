# Public benchmark site

Overview (`benchmarks.html`) explains the task, scoring contract, and limits.
Evidence (`benchmark-evidence.html`) is the interactive results workspace.
Models, Scenarios, Providers, Coverage, and History provide focused views.

Evidence uses the shared Jinja layout plus scoped `static/evidence.css` and
`static/evidence.js`. The publisher embeds the public catalog using Jinja's
`tojson` filter: no review-directory dependency, live inference, or API fetch
is required. Keep template and static sources here; `docs/` is generated.

The old Compare and Explore URLs redirect to Evidence, preserving query
parameters. Legacy `lane` selections match release, exact model ID, and
reasoning; if multiple providers match, all remain visible. New `pin`
identities include provider. Never infer canonical model aliases or pool
different releases into one score.

Scenario filters affect matrix columns and the inspector. The explicitly
labeled whole-cohort totals and cost summaries remain whole-cohort totals.
Selected comparison rows ignore other filters except scenario.

Run `make site-build pages-check` after edits. Page checks include generated
asset parity and the dependency-free Node interaction suite when Node is
available. Run `node tests/evidence.test.cjs` directly to require those checks.
The DOM shim tests state and rendering logic, not browser layout; inspect the
responsive design in a browser before publishing.

The Scores page keeps its equal-weight headline and adds per-scenario outcome
cells linking to the matching release/model/provider/reasoning evidence.
Observed scenario failure rates are descriptive aggregates of complete,
release-scoped configuration entries within one benchmark-condition group.
They follow the harness filter, include unavailable attempts as unsuccessful,
and show their recorded-attempt and configuration counts. They never alter
headline scores or combine different scenario versions/condition groups.
Repeating a configuration in another explicitly selected release adds another
sample entry; no newest/best-run choice or deduplication by model is inferred.

Scenario cells and names open an accessible native dialog with the matching
condition/harness sample and per-configuration outcomes. Each row links to
its exact evidence; the clicked configuration is highlighted. Escape, the
close button, or clicking the backdrop dismisses the overlay and restores
focus. Without dialog support, cells retain direct evidence links. Modal
content is inserted as text, with JSON escaped through Jinja's `tojson` filter.
