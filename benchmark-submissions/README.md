# Benchmark submissions

This directory contains content-addressed benchmark evidence submitted for
independent review. Each JSON bundle contains normalized matrix results and the
frozen compatibility metadata needed to verify them. Bundles exclude local
paths, credentials, transcripts, VM images, and other private run artifacts.

Pull requests that modify this directory are validated by GitHub Actions.
Merging a bundle records the evidence but does not automatically promote it to
the public benchmark. Maintainers review provenance and decide whether to
import it into a dated benchmark release.
