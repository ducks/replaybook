"""Offline configuration tests: no real credentials or inference."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from integrations.host.run_host_matrix import preflight_agent_config
from integrations.host.publish_benchmarks import compatibility_snapshot

ROOT = Path(__file__).resolve().parent
ADAPTER = ROOT / "adapters/opencode.sh"


class ConfigTests(unittest.TestCase):
    def test_publisher_separates_pinned_and_unpinned_configs(self):
        base = {"scenario_packs": []}
        pinned = {**base, "agent_config_sha256": "a" * 64}
        self.assertNotEqual(compatibility_snapshot(base), compatibility_snapshot(pinned))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.definition = {
            "schema_version": 1,
            "providers": {"test-provider": {
                "base_url": "https://example.invalid/v1",
                "models": {"test-model": {
                    "id": "test-model", "name": "Test", "tool_call": True,
                    "provider": {"npm": "@ai-sdk/google"},
                    "limit": {"context": 1000, "output": 100},
                }},
            }},
        }
        self.config.write_text(json.dumps(self.definition))

    def render(self, model="test-provider/test-model"):
        return subprocess.run(["bash", str(ADAPTER), "--print-config", str(self.config), model],
                              text=True, capture_output=True)

    def test_arbitrary_provider_and_model_are_data(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["enabled_providers"], ["test-provider"])
        provider = config["provider"]["test-provider"]
        self.assertEqual(provider["options"]["baseURL"], "https://example.invalid/v1")
        self.assertEqual(provider["models"]["test-model"]["provider"]["npm"], "@ai-sdk/google")
        self.assertNotIn("apiKey", result.stdout)

    def test_missing_model_fails_before_payload(self):
        with self.assertRaisesRegex(ValueError, "missing or invalid"):
            preflight_agent_config(ADAPTER, self.config, Path("/does-not-exist"), ["test-provider/missing"])

    def test_invalid_definition_fails_closed(self):
        for change in ("schema", "endpoint", "sdk", "identity"):
            with self.subTest(change=change):
                config = json.loads(json.dumps(self.definition))
                provider = config["providers"]["test-provider"]
                if change == "schema":
                    config["schema_version"] = 99
                elif change == "endpoint":
                    provider["base_url"] = "http://example.invalid"
                elif change == "sdk":
                    del provider["models"]["test-model"]["provider"]
                else:
                    provider["models"]["test-model"]["id"] = "substitution"
                self.config.write_text(json.dumps(config))
                self.assertNotEqual(self.render().returncode, 0)

    def test_guest_adapter_receives_pinned_config(self):
        results = self.root / "results"
        results.mkdir()
        instruction = self.root / "instruction.md"
        instruction.write_text("Synthetic test")
        payload = self.root / "payload"
        payload.write_text('''#!/usr/bin/env python3
import json, os
config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert config["model"] == "test-provider/test-model"
assert config["provider"]["test-provider"]["models"]["test-model"]["provider"]["npm"] == "@ai-sdk/google"
assert os.environ["OPENCODE_DISABLE_MODELS_FETCH"] == "true"
print(json.dumps({"type":"text", "part":{"text":"ok"}}))
''')
        payload.chmod(0o755)
        env = dict(os.environ, REPLAYBOOK_EVAL_ROOT=str(self.root),
                   REPLAYBOOK_AGENT_PAYLOAD=str(payload), REPLAYBOOK_AGENT_CONFIG=str(self.config),
                   REPLAYBOOK_MODEL="test-provider/test-model",
                   REPLAYBOOK_INSTRUCTION_FILE=str(instruction), REPLAYBOOK_WORKSPACE=str(self.root),
                   REPLAYBOOK_RESULT_FILE=str(results / "agent.json"),
                   REPLAYBOOK_TRANSCRIPT_FILE=str(results / "transcript.json"),
                   OPENCODE_AUTH_JSON_B64=base64.b64encode(b'{}').decode())
        run = subprocess.run(["bash", str(ADAPTER)], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads((results / "agent.json").read_text())["result"], "ok")
