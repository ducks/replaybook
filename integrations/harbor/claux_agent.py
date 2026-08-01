"""Harbor adapter for running Claux inside an evaluation container."""

import json
import math
import shlex
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


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


class ClauxAgent(BaseInstalledAgent):
    """Install a released Claux binary and run it headlessly via OpenRouter."""

    _DEFAULT_RELEASE_TAG = "v20260730.0.1"

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
                "--output-format json | tee /logs/agent/claux.json"
            ),
        )
        payload = parse_claux_output(result.stdout or "")
        populate_context(payload, context)
