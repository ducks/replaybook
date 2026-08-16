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
