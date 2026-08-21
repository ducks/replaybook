# Repository instructions

## Workflow

- Work on a feature branch. Do not commit directly to `main`.
- Preserve unrelated local changes and job artifacts.
- Use merge commits (`git merge --no-ff`) when a completed feature branch is
  merged into `main`.
- Do not push, publish benchmarks, create releases, or merge into `main` unless
  the user explicitly asks.

## Project boundaries

- Replaybook owns the Rust CLI, host and Harbor harnesses, result processing,
  benchmark publication, and bundled agent skills.
- The public host-native scenario pack lives in `../replaybook-infra`. Add new
  benchmark scenarios there unless the task specifically concerns a bundled
  fixture or legacy scenario in this repository.
- Never commit provider credentials, environment files, private transcripts,
  customer data, or unreviewed contents of `jobs/`.
- Keep oracles, verifier internals, expected repairs, and proxy-held secrets
  outside the agent-visible guest image and prompt.

## Benchmark integrity

- Treat the host controller and verifier as the scoring boundary. Agent
  adapters may change how a harness runs, but must not change what counts as a
  successful repair.
- Keep results from different harnesses, scenario versions, tiers, attempt
  counts, timeouts, or provider boundaries visibly separate unless the
  publisher proves they are compatible.
- Count provider and harness failures as unavailable only when meaningful
  inference did not occur. Do not hide evaluated failures by reclassifying
  them as infrastructure failures.
- Preserve immutable execution snapshots, resumability, raw evidence, and the
  harness-neutral trajectory export when changing matrix execution.
- Publish claims only from tracked, inspectable result artifacts. Never hand
  edit pass rates or aggregate totals.

## Generated benchmark files

- `integrations/host/publish_benchmarks.py` is the source of truth for generated
  benchmark HTML and JSON.
- After changing the publisher or tracked benchmark data, run:

  ```sh
  python integrations/host/publish_benchmarks.py build
  python integrations/host/publish_benchmarks.py check
  ```

- Commit the regenerated `benchmark-data/catalog.json`,
  `benchmark-data/coverage.json`, and corresponding files under `docs/` with
  the source change.
- Static Pages changes must continue to pass `make pages-check`.

## Validation

- Run the narrowest relevant tests while iterating, then the matching project
  check before handing off:

  - Rust CLI or engine: `cargo test`, `cargo fmt -- --check`, and
    `cargo clippy -- -D warnings`.
  - Host-native harness, adapters, matrices, or benchmark publisher:
    `make host-check`.
  - Harbor integration: `make harbor-check`.
  - Bundled agent skills: `make skills-check`.
  - GitHub Pages and public site: `make pages-check`.

- Run `make lint` for broad or release-facing changes.
- Every scoring scenario change needs its scenario/pack version bumped and an
  oracle run proving the fresh VM can pass immediate, service-restart, and
  host-reboot verification.

## Shell and artifact safety

- Prefer commands that work in Bash and provide Nushell-safe variants when the
  user will run them interactively.
- Validate host ports and available disk space before launching a large VM
  matrix. Use resume rather than restarting completed trials.
- Do not delete retained jobs, Nix store paths, VM images, or benchmark
  artifacts without resolving the exact targets and receiving explicit user
  approval.
