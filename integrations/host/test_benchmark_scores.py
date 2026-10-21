import copy
from types import SimpleNamespace
import unittest
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .benchmark_scores import score_release, build_scores, condition_id, group_conditions


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = SimpleNamespace(
            id="example-core", version="1", tier="core", sha256="contract",
            attempts=3, agent_timeout_seconds=900, required_host_harness_version=23,
            scenarios=[SimpleNamespace(id=name, version=1) for name in ("a", "b")],
        )
        self.release = {
            "version": "20261015.0.0", "labels": {},
            "compatibility": {
                "benchmark_manifest": {"id": "example-core", "version": "1", "tier": "core", "sha256": "contract"},
                "attempts": 3, "agent_timeout_seconds": 900, "harness_version": 23,
                "agent_harness": {"id": "claux", "label": "Claux", "version": "test", "provider": "test", "billing": "provider-reported"},
            },
            "by_scenario_model": [self.row("a", 3, 3), self.row("b", 0, 3)],
        }

    def row(self, scenario, passed, evaluated, unavailable=0):
        return dict(scenario=scenario, scenario_version=1, model="model", provider="test",
                    reasoning_effort="high", passed=passed, evaluated=evaluated,
                    unavailable=unavailable, trials=evaluated + unavailable)

    def card(self):
        return score_release(self.release, self.benchmark)["cards"][0]

    def test_complete(self):
        self.assertEqual(self.card()["score"], 50)
        self.assertEqual(self.card()["status"], "complete")

    def test_equal_scenario_weight_not_trial_weight(self):
        self.release["by_scenario_model"][0] = self.row("a", 30, 30)
        self.assertEqual(self.card()["score"], 50)
        self.assertEqual(self.card()["status"], "provisional")

    def test_missing_version_does_not_count_as_failure_or_complete(self):
        self.release["by_scenario_model"][1]["scenario_version"] = 2
        self.assertEqual(self.card()["score"], 100)
        self.assertEqual(self.card()["covered"], 1)
        self.assertEqual(self.card()["status"], "provisional")

    def test_unavailable_counts_against_headline_not_conditional_repair(self):
        self.release["by_scenario_model"][0] = self.row("a", 2, 2, 1)
        self.assertAlmostEqual(self.card()["score"], 100 / 3)
        self.assertEqual(self.card()["repair_score"], 50)
        self.assertEqual(self.card()["unavailable"], 1)
        self.assertEqual(self.card()["status"], "complete")

    def test_all_unavailable_is_zero_not_missing(self):
        self.release["by_scenario_model"] = [self.row("a", 0, 0, 3)]
        self.assertEqual(self.card()["score"], 0)
        self.assertIsNone(self.card()["repair_score"])
        self.assertEqual(self.card()["covered"], 1)
        self.assertEqual(self.card()["repair_covered"], 0)
        self.assertEqual(self.card()["status"], "provisional")

    def test_no_recorded_trials_no_score(self):
        self.release["by_scenario_model"] = [self.row("a", 0, 0)]
        self.assertIsNone(self.card()["score"])
        self.assertIsNone(self.card()["repair_score"])

    def test_unavailable_scenario_not_dropped_from_average(self):
        self.release["by_scenario_model"] = [self.row("a", 3, 3), self.row("b", 0, 0, 3)]
        self.assertEqual(self.card()["score"], 50)
        self.assertEqual(self.card()["repair_score"], 100)
        self.assertEqual(self.card()["repair_covered"], 1)
        self.assertEqual(self.card()["status"], "complete")

    def test_contract_mismatch_is_never_complete(self):
        self.release["compatibility"]["benchmark_manifest"]["sha256"] = "other"
        self.assertEqual(self.card()["status"], "provisional")

    def test_providers_and_reasoning_stay_separate(self):
        other = copy.deepcopy(self.release["by_scenario_model"])
        for row in other:
            row["provider"] = "other"
        self.release["by_scenario_model"].extend(other)
        self.assertEqual(len(score_release(self.release, self.benchmark)["cards"]), 2)

    def test_duplicates_rejected(self):
        self.release["by_scenario_model"].append(self.row("a", 3, 3))
        with self.assertRaises(ValueError):
            self.card()

    def test_extra_scenario_cannot_receive_complete_score(self):
        self.release["by_scenario_model"].append(self.row("unexpected", 3, 3))
        self.assertEqual(self.card()["status"], "provisional")

    def test_public_cohort_links_and_scores(self):
        root = Path(__file__).resolve().parents[2]
        index = json.loads((root / "benchmark-data/index.json").read_text())
        report = build_scores(root, index)
        self.assertTrue(report["cohorts"])
        for cohort in report["cohorts"]:
            for provider, cards in cohort["groups"].items():
                for card in cards:
                    self.assertEqual(card["status"], "complete")
                    self.assertEqual(card["covered"], card["required"])
                    params = parse_qs(urlparse(card["evidence_url"]).query)
                    self.assertEqual(params["provider"], [provider])
                    self.assertEqual(params["model"], [card["model"]])
                    self.assertEqual(params["release"], [card["release"]])
                    self.assertEqual(params["harness"], [card["harness"]["id"]])
                    self.assertIn("reasoning", params)

    def test_incomplete_cards_hidden_from_public_groups(self):
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[2]
        index = json.loads((root / "benchmark-data/index.json").read_text())
        report = score_release(self.release, self.benchmark)
        report["cards"][0]["status"] = "provisional"
        with patch("integrations.host.benchmark_scores.score_release", return_value=report):
            public = build_scores(root, index)
        self.assertTrue(all(not c["groups"] for c in public["cohorts"]))
        self.assertTrue(all(c["hidden"] == len(c["releases"]) for c in public["cohorts"]))

    def test_non_core_rejected(self):
        self.benchmark.tier = "smoke"
        with self.assertRaises(ValueError):
            self.card()

    def snapshot_release(self):
        release = copy.deepcopy(self.release)
        release["compatibility"]["execution_snapshot"] = {
            "host_harness_sha256": "controller",
            "selected_scenarios": [{"id": "a", "pack_id": "pack", "version": 1, "sha256": "content"}],
        }
        return release

    def test_agent_configuration_can_vary_without_changing_conditions(self):
        first = self.snapshot_release()
        other = copy.deepcopy(first)
        other["version"] = "other"
        other["compatibility"]["agent_harness"]["id"] = "opencode"
        other["compatibility"]["providers"] = ["another-provider"]
        other["compatibility"]["execution_snapshot"]["agent_payload_sha256"] = "new-agent"
        self.assertEqual(condition_id(first), condition_id(other))

    def test_changed_conditions_stay_separate(self):
        first = self.snapshot_release()
        for key, value in (("attempts", 4), ("agent_timeout_seconds", 1800), ("harness_version", 24)):
            other = copy.deepcopy(first)
            other["compatibility"][key] = value
            self.assertNotEqual(condition_id(first), condition_id(other))
        for key, value in (("host_harness_sha256", "new"), ("selected_scenarios", [])):
            other = copy.deepcopy(first)
            other["compatibility"]["execution_snapshot"][key] = value
            self.assertNotEqual(condition_id(first), condition_id(other))
        other = copy.deepcopy(first)
        other["compatibility"]["execution_snapshot"]["selected_scenarios"][0]["sha256"] = "changed"
        self.assertNotEqual(condition_id(first), condition_id(other))

    def test_unknown_provenance_is_isolated_by_release(self):
        other = copy.deepcopy(self.release)
        other["version"] = "other"
        self.assertNotEqual(condition_id(self.release), condition_id(other))

    def test_grouping_preserves_cards_instead_of_pooling_attempts(self):
        cohorts = [dict(id="same", release=release, hidden=0,
                        groups={provider: [{"release": release, "score": score}]})
                   for release, provider, score in [("a", "openrouter", 50), ("b", "go", 100), ("c", "openrouter", 75)]]
        grouped = group_conditions(cohorts)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["releases"], ["a", "b", "c"])
        self.assertEqual([c["score"] for c in grouped[0]["groups"]["openrouter"]], [50, 75])


if __name__ == "__main__":
    unittest.main()
