"""Tests for Claux's Harbor telemetry adapter."""

import json
import unittest

from harbor.models.agent.context import AgentContext

from integrations.harbor.claux_agent import (
    parse_claux_output,
    parse_claux_transcript,
    populate_context,
    transcript_to_trajectory,
)


def output_with_usage(**usage_overrides):
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 80,
        "cache_creation_tokens": 10,
        "cost_usd": 0.00125,
    }
    usage.update(usage_overrides)
    return json.dumps(
        {
            "schema_version": 1,
            "result": "done",
            "model": "deepseek/deepseek-v4-flash",
            "usage": usage,
        }
    )


def transcript_with_trace(**overrides):
    transcript = {
        "schema_version": 1,
        "model": "deepseek/deepseek-v4-flash",
        "outcome": {"status": "completed", "result": "service repaired"},
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 80,
            "cache_creation_tokens": 10,
            "cost_usd": 0.00125,
        },
        "messages": [{"role": "user", "content": "repair the service"}],
        "tool_trace": [
            {
                "id": "tool-1",
                "name": "Read",
                "input": {"file_path": "/run/secrets/rotated-auth.env"},
                "output": "AUTH_TOKEN=rotated-token\n",
                "is_error": False,
            },
            {
                "id": "tool-2",
                "name": "Bash",
                "input": {"command": "systemctl restart app"},
                "output": "permission denied",
                "is_error": True,
            },
        ],
    }
    transcript.update(overrides)
    return json.dumps(transcript)


class ParseClauxOutputTests(unittest.TestCase):
    def test_populates_harbor_context(self):
        context = AgentContext(metadata={"existing": True})

        populate_context(parse_claux_output(output_with_usage()), context)

        self.assertEqual(context.n_input_tokens, 100)
        self.assertEqual(context.n_output_tokens, 20)
        self.assertEqual(context.n_cache_tokens, 90)
        self.assertEqual(context.cost_usd, 0.00125)
        self.assertEqual(context.metadata["existing"], True)
        self.assertEqual(
            context.metadata["claux"],
            {
                "schema_version": 1,
                "model": "deepseek/deepseek-v4-flash",
                "cache_read_tokens": 80,
                "cache_creation_tokens": 10,
            },
        )

    def test_preserves_unknown_cost(self):
        context = AgentContext()

        populate_context(
            parse_claux_output(output_with_usage(cost_usd=None)), context
        )

        self.assertIsNone(context.cost_usd)

    def test_rejects_unsupported_schema(self):
        output = json.loads(output_with_usage())
        output["schema_version"] = 2

        with self.assertRaisesRegex(ValueError, "unsupported JSON schema"):
            parse_claux_output(json.dumps(output))

    def test_rejects_invalid_token_counts(self):
        for invalid in (-1, True, "100"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "input_tokens"):
                    parse_claux_output(output_with_usage(input_tokens=invalid))

    def test_rejects_invalid_costs(self):
        for invalid in (-0.1, float("inf"), float("nan"), True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "cost_usd"):
                    parse_claux_output(output_with_usage(cost_usd=invalid))

    def test_rejects_non_json_output(self):
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            parse_claux_output("not json")


class ClauxTranscriptTests(unittest.TestCase):
    def test_converts_exact_tool_calls_and_outputs_to_atif(self):
        transcript = parse_claux_transcript(transcript_with_trace())

        trajectory = transcript_to_trajectory(
            transcript,
            agent_version="20260804.0.0",
            session_id="trial-agent",
        )
        value = trajectory.to_json_dict()

        self.assertEqual(value["schema_version"], "ATIF-v1.7")
        self.assertEqual(value["session_id"], "trial-agent")
        self.assertEqual(value["agent"]["name"], "claux")
        self.assertEqual(value["steps"][0]["source"], "user")
        read_step = value["steps"][1]
        self.assertEqual(read_step["tool_calls"][0]["function_name"], "Read")
        self.assertEqual(
            read_step["observation"]["results"][0]["content"],
            "AUTH_TOKEN=rotated-token\n",
        )
        failed_step = value["steps"][2]
        self.assertTrue(
            failed_step["observation"]["results"][0]["extra"]["is_error"]
        )
        self.assertEqual(value["steps"][-1]["message"], "service repaired")
        self.assertEqual(value["final_metrics"]["total_cached_tokens"], 90)
        self.assertEqual(value["final_metrics"]["total_cost_usd"], 0.00125)

    def test_rejects_invalid_trace_entry(self):
        transcript = json.loads(transcript_with_trace())
        transcript["tool_trace"][0]["output"] = None

        with self.assertRaisesRegex(ValueError, "invalid tool trace entry"):
            parse_claux_transcript(json.dumps(transcript))

    def test_records_failed_outcome(self):
        transcript = json.loads(transcript_with_trace())
        transcript["outcome"] = {"status": "error", "message": "disconnected"}

        trajectory = transcript_to_trajectory(
            parse_claux_transcript(json.dumps(transcript)),
            agent_version="20260804.0.0",
            session_id=None,
        )

        self.assertEqual(trajectory.steps[-1].message, "disconnected")
        self.assertEqual(trajectory.steps[-1].extra, {"outcome": "error"})


if __name__ == "__main__":
    unittest.main()
