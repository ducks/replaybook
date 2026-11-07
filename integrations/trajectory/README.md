# Infrastructure agent trajectories

Replaybook exports retained host-agent runs using the Agent Trajectory
Interchange Format, currently `ATIF-v1.7`. The same format and infrastructure
evaluation extension are emitted by Agents of Empires, so repair trials and
competitive matches can feed the same analysis or training pipeline.

Export one trial or a complete matrix:

```sh
python integrations/trajectory/export_atif.py \
  jobs/host-matrix-YYYY-MM-DD__HH-MM-SS.XXXXXX \
  --output trajectories
```

Each output file contains:

- the original user task;
- ordered observable tool calls with exact arguments and outputs;
- tool errors, timing, and read-only classification;
- the final agent response and outcome;
- token, cache, cost, and model-round accounting; and
- a versioned `infrastructure_evaluation` object under
  `final_metrics.extra`.

The shared extension has schema version `1` and four sections:

- `producer`: the evaluation system and artifact version;
- `task`: the scenario or arena identity and version;
- `execution`: harness, model, reasoning, budget, and timing configuration;
- `outcome`: objective verifier results, durability, reward, and failures.

The exporter intentionally excludes private assistant messages and hidden
reasoning. Tool inputs and outputs are preserved because they are the grounded
behavioral record. They may contain credentials, customer data, internal host
names, or other incident secrets. Treat exported trajectories as sensitive
until they have passed a separate redaction and publication review.

ATIF is the interchange layer, not the benchmark score. Replaybook remains the
authority for whether a repair passed its immediate, service-restart, and
host-reboot verification.

## Curate a versioned dataset

`curate.py` adds local review and dataset release controls on top of the existing
ATIF exporter. It does not train a model, send data to a provider, approve legal
rights, or publish anything. Its inventories and review copies should stay private.

```sh
python integrations/trajectory/curate.py index /path/to/matrix \
  --output /private/curation.json
python integrations/trajectory/curate.py summary /private/curation.json
python integrations/trajectory/curate.py prepare /private/curation.json TRIAL_ID_PREFIX \
  --output /private/reviewed.json
```

Inspect the prepared ATIF file. Redact secrets and personal/customer data, remove
redundant exploration if appropriate, and check that the resulting example still
teaches a defensible repair. No automatic redactor is claimed. Source result and
transcript hashes bind provenance; the reviewed copy gets its own hash. Editing
a source or an approved copy invalidates the earlier approval. Identity and
verifier metadata cannot be rewritten in a reviewed copy.

The index retains failures for analysis. `review` can reject examples and attach
failure tags and notes. Version 1 exports only positive examples: successful
repairs with passing immediate, service-restart and host-reboot checks, accepted
quality, approved privacy, and separately approved training rights with evidence.
Passes start unreviewed and training rights start unknown. Provider outputs are
not automatically training-licensed because the incident code is public.

Example approval syntax, **only after those reviews have actually happened**:

```sh
python integrations/trajectory/curate.py review /private/curation.json TRIAL_ID_PREFIX \
  --family proxy-routing --reviewer YOUR_REVIEWER_ID \
  --quality accepted --privacy approved --rights approved \
  --rights-evidence 'Reference to the applicable reviewed permission' \
  --copy /private/reviewed.json --tag durable-repair
```

Assign an incident **family**, not an individual attempt, to each dataset split.
All model attempts and versions of the same scenario are also kept together,
even if a reviewer accidentally labels their families differently. An explicit
split plan has this form:

```json
{
  "train": ["proxy-routing", "worker-queue"],
  "validation": ["certificate-trust"],
  "test": ["shared-storage"]
}
```

```sh
python integrations/trajectory/curate.py export /private/curation.json \
  --splits /private/splits.json --version sre-pilot-1 \
  --output /private/datasets/sre-pilot-1
```

The immutable output directory contains `train.jsonl`, `validation.jsonl`,
`test.jsonl`, and `manifest.json`, with counts, provenance and file hashes. Each
JSONL record wraps an ATIF trajectory with its ID, family and review tags. It is
not a model-specific SFT chat format; a trainer still needs the matching tool
schemas and model formatting. A failed validation leaves no partial dataset.
Exact observable duplicates are prohibited across splits, ignoring incidental
model names, call IDs and timing. Semantic near-duplicate detection is not included.
Family labels and rights evidence remain human assertions, not proof of legality
or generalization. Review notes and local source paths are not put in the dataset
manifest. Exported tool arguments/observations still need the privacy review.

Current limitations: local CLI only; no review web UI, training runner, tokenization,
preference-pair mining, reward shaping, automatic permission checks, automatic
quality approval, or trained-agent improvement claims. Raw OpenCode event arrays
are reported as unsupported rather than converted into misleading examples.

Run `make trajectory-check` for deterministic tests. Tests use synthetic,
explicitly fixture-only permissions; they do not approve any real provider data.
