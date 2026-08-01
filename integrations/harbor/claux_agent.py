"""Minimal Harbor adapter for running Claux inside an evaluation container."""

import shlex
from typing import override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


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
        del context
        if not self.model_name:
            raise ValueError("Claux requires an OpenRouter model ID")
        if not self._has_env("OPENROUTER_API_KEY"):
            raise ValueError(
                "Claux requires OPENROUTER_API_KEY; pass it with Harbor's --agent-env"
            )

        model = shlex.quote(self.model_name)
        prompt = shlex.quote(instruction)
        await self.exec_as_agent(
            environment,
            command=(
                "mkdir -p /logs/agent && "
                f"claux config init --provider openrouter --model {model} && "
                f"claux --print {prompt} --permission-mode bypass "
                "2>&1 | tee /logs/agent/claux.txt"
            ),
        )
