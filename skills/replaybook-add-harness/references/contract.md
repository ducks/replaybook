# Replaybook harness adapter contract

## Controller inputs

Replaybook stages the adapter in a disposable NixOS VM and exports:

| Variable | Meaning |
| --- | --- |
| `REPLAYBOOK_EVAL_ROOT` | Private staging and result directory. |
| `REPLAYBOOK_INSTRUCTION_FILE` | Scenario-authored incident instruction. |
| `REPLAYBOOK_MODEL` | Exact scheduled model identifier. |
| `REPLAYBOOK_WORKSPACE` | Working root available to the agent. |
| `REPLAYBOOK_RESULT_FILE` | Required normalized JSON result path. |
| `REPLAYBOOK_TRANSCRIPT_FILE` | Optional harness-native JSON transcript path. |
| `REPLAYBOOK_AGENT_PAYLOAD` | Optional staged binary or artifact. |

The environment file is sourced with automatic export immediately before the
adapter runs. It is private to the disposable VM and should contain only the
credentials and settings required for that run.

## Required result

Write one JSON object to `REPLAYBOOK_RESULT_FILE`:

```json
{
  "schema_version": 1,
  "harness": "example-agent",
  "model": "vendor/model",
  "result": "final agent response",
  "usage": {
    "input_tokens": 123,
    "output_tokens": 45,
    "cache_read_tokens": 100,
    "cache_creation_tokens": 0,
    "cost_usd": null
  },
  "outcome": {
    "status": "success"
  }
}
```

Required fields are `schema_version`, `harness`, and `model`. The harness must
equal `--agent-name`; the model must equal the scheduled model. `result`,
`usage`, and `outcome` may be null when the harness fails or is cancelled.

Use atomic writes: write `<result>.partial`, then rename it. Replaybook captures
the result before it restarts services or reboots the host.

## Exit semantics

- Exit zero only when the harness completed normally and wrote a valid result.
- Return the harness exit status for authentication, startup, model, or tool
  failures, even when a partial normalized result exists.
- Forward termination to the child so Replaybook's timeout remains bounded.
- Produce a partial normalized result when the native event stream contains
  reliable usage or outcome data.

Replaybook owns the score. An adapter must not run the verifier, claim reward,
or translate its final prose into a pass/fail result.

## Transcript

The transcript is optional and harness-defined. Prefer a valid JSON array or
object containing native events. Do not silently discard command executions,
tool errors, or cancellation events. Never put credentials into a transcript.

## Invocation

Single run:

```sh
integrations/host/run-host-native.sh \
  --scenario 001-nginx-502-host \
  --model vendor/model \
  --agent-adapter integrations/host/adapters/example-agent.sh \
  --agent-payload /path/to/example-agent \
  --agent-env-file /path/to/example-agent.env \
  --agent-name example-agent
```

Matrix run:

```sh
python integrations/host/run_host_matrix.py \
  --scenario 001-nginx-502-host \
  --models vendor/model \
  --agent-adapter integrations/host/adapters/example-agent.sh \
  --agent-payload /path/to/example-agent \
  --agent-env-file /path/to/example-agent.env \
  --agent-name example-agent \
  --attempts 1 \
  --concurrency 1
```

Keep Nushell commands on one line unless using Nushell's own multiline syntax.
Backslash continuation is a POSIX-shell feature and causes confusing Nushell
parse errors.
