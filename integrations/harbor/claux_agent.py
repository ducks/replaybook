"""Harbor adapter for running Claux inside an evaluation container."""

import json
import math
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.utils.trajectory_utils import format_trajectory_json


def parse_claux_output(stdout: str) -> dict[str, Any]:
    """Parse and validate Claux's versioned one-shot JSON output."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Claux returned invalid JSON output") from error

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Claux returned an unsupported JSON schema version")
    if not isinstance(payload.get("result"), str):
        raise ValueError("Claux JSON result must be a string")
    if not isinstance(payload.get("model"), str):
        raise ValueError("Claux JSON model must be a string")

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Claux JSON usage must be an object")

    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    ):
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Claux JSON {field} must be a non-negative integer")

    cost = usage.get("cost_usd")
    if cost is not None:
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or cost < 0
        ):
            raise ValueError("Claux JSON cost_usd must be null or non-negative")

    return payload


def populate_context(payload: dict[str, Any], context: AgentContext) -> None:
    """Copy validated Claux usage into Harbor's trial context."""
    usage = payload["usage"]
    context.n_input_tokens = usage["input_tokens"]
    context.n_output_tokens = usage["output_tokens"]
    context.n_cache_tokens = (
        usage["cache_read_tokens"] + usage["cache_creation_tokens"]
    )
    context.cost_usd = usage["cost_usd"]
    context.metadata = {
        **(context.metadata or {}),
        "claux": {
            "schema_version": payload["schema_version"],
            "model": payload["model"],
            "cache_read_tokens": usage["cache_read_tokens"],
            "cache_creation_tokens": usage["cache_creation_tokens"],
        },
    }


def parse_claux_transcript(raw: str) -> dict[str, Any]:
    """Parse and validate the fields used from Claux's transcript artifact."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Claux returned an invalid transcript") from error

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Claux returned an unsupported transcript schema version")
    if not isinstance(payload.get("model"), str):
        raise ValueError("Claux transcript model must be a string")

    outcome = payload.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("status") not in {
        "completed",
        "error",
    }:
        raise ValueError("Claux transcript outcome is invalid")
    if outcome["status"] == "completed" and not isinstance(
        outcome.get("result"), str
    ):
        raise ValueError("Claux completed transcript result must be a string")
    if outcome["status"] == "error" and not isinstance(outcome.get("message"), str):
        raise ValueError("Claux failed transcript message must be a string")

    trace = payload.get("tool_trace")
    if not isinstance(trace, list):
        raise ValueError("Claux transcript tool_trace must be an array")
    for entry in trace:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("input"), dict)
            or not isinstance(entry.get("output"), str)
            or not isinstance(entry.get("is_error"), bool)
        ):
            raise ValueError("Claux transcript contains an invalid tool trace entry")

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Claux transcript messages must be an array")

    # Reuse the one-shot validator for the shared usage contract.
    result = outcome["result"] if outcome["status"] == "completed" else ""
    parse_claux_output(
        json.dumps(
            {
                "schema_version": 1,
                "result": result,
                "model": payload["model"],
                "usage": payload.get("usage"),
            }
        )
    )
    return payload


def transcript_to_trajectory(
    payload: dict[str, Any], *, agent_version: str, session_id: str | None
) -> Trajectory:
    """Convert Claux's ordered tool trace into Harbor's ATIF format."""
    steps: list[Step] = []

    for message in payload["messages"]:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            steps.append(Step(step_id=1, source="user", message=content))
            break

    for entry in payload["tool_trace"]:
        call_id = entry["id"]
        steps.append(
            Step(
                step_id=len(steps) + 1,
                source="agent",
                model_name=payload["model"],
                message="",
                tool_calls=[
                    ToolCall(
                        tool_call_id=call_id,
                        function_name=entry["name"],
                        arguments=entry["input"],
                    )
                ],
                observation=Observation(
                    results=[
                        ObservationResult(
                            source_call_id=call_id,
                            content=entry["output"],
                            extra={"is_error": entry["is_error"]},
                        )
                    ]
                ),
            )
        )

    outcome = payload["outcome"]
    final_message = (
        outcome.get("result", "")
        if outcome["status"] == "completed"
        else outcome.get("message", "Claux run failed")
    )
    steps.append(
        Step(
            step_id=len(steps) + 1,
            source="agent",
            model_name=payload["model"],
            message=final_message,
            extra={"outcome": outcome["status"]},
        )
    )

    usage = payload["usage"]
    cached_tokens = usage["cache_read_tokens"] + usage["cache_creation_tokens"]
    return Trajectory(
        session_id=session_id,
        agent=Agent(
            name="claux",
            version=agent_version,
            model_name=payload["model"],
        ),
        steps=steps,
        notes=(
            "Claux records complete ordered tool calls and outputs independently "
            "of context compaction. Assistant reasoning between calls is not emitted."
        ),
        final_metrics=FinalMetrics(
            total_prompt_tokens=usage["input_tokens"],
            total_completion_tokens=usage["output_tokens"],
            total_cached_tokens=cached_tokens,
            total_cost_usd=usage["cost_usd"],
            total_steps=len(steps),
            extra={
                "cache_read_tokens": usage["cache_read_tokens"],
                "cache_creation_tokens": usage["cache_creation_tokens"],
            },
        ),
    )


class ClauxAgent(BaseInstalledAgent):
    """Install a released Claux binary and run it headlessly via OpenRouter."""

    SUPPORTS_ATIF = True
    _DEFAULT_RELEASE_TAG = "v20260804.0.0"

    def __init__(self, *args, release_tag: str | None = None, **kwargs):
        self._release_tag = release_tag or self._DEFAULT_RELEASE_TAG
        kwargs.setdefault("version", self._release_tag.removeprefix("v"))
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "claux"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        url = shlex.quote(
            "https://github.com/ducks/claux/releases/download/"
            f"{self._release_tag}/claux-linux-x86_64"
        )
        await self.exec_as_root(
            environment,
            command=(
                f"curl --fail --location --silent --show-error {url} "
                "--output /usr/local/bin/claux && "
                "chmod 0755 /usr/local/bin/claux && "
                "claux --help >/dev/null"
            ),
        )

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError("Claux requires an OpenRouter model ID")
        if not self._has_env("OPENROUTER_API_KEY"):
            raise ValueError(
                "Claux requires OPENROUTER_API_KEY; pass it with Harbor's --agent-env"
            )

        model = shlex.quote(self.model_name)
        prompt = shlex.quote(instruction)
        result = await self.exec_as_agent(
            environment,
            command=(
                "mkdir -p /logs/agent && "
                f"claux config init --provider openrouter --model {model} "
                ">/dev/null && "
                f"claux --print {prompt} --permission-mode bypass "
                "--output-format json "
                "--transcript /logs/agent/claux-transcript.json "
                "| tee /logs/agent/claux.json"
            ),
        )
        # Validate the command's public output now. Telemetry is populated after
        # Harbor has copied the transcript and one-shot artifacts to the host.
        parse_claux_output(result.stdout or "")

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        output_path = self.logs_dir / "claux.json"
        try:
            payload = parse_claux_output(output_path.read_text())
            populate_context(payload, context)
        except (OSError, ValueError):
            self.logger.exception("Failed to load Claux one-shot telemetry")
            return

        transcript_path = self.logs_dir / "claux-transcript.json"
        try:
            transcript = parse_claux_transcript(transcript_path.read_text())
            trajectory = transcript_to_trajectory(
                transcript,
                agent_version=self._release_tag.removeprefix("v"),
                session_id=self.session_id,
            )
            self._write_trajectory(trajectory, self.logs_dir / "trajectory.json")
        except (OSError, ValueError):
            self.logger.exception("Failed to convert Claux transcript to ATIF")

    @staticmethod
    def _write_trajectory(trajectory: Trajectory, path: Path) -> None:
        path.write_text(format_trajectory_json(trajectory.to_json_dict()))
