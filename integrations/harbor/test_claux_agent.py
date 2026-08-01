"""Tests for Claux's Harbor telemetry adapter."""

import json
import unittest

from harbor.models.agent.context import AgentContext

from integrations.harbor.claux_agent import parse_claux_output, populate_context


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


if __name__ == "__main__":
    unittest.main()
