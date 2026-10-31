import copy
from types import SimpleNamespace
import unittest
import json
import tempfile
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .benchmark_scores import score_release, build_scores, condition_id, group_conditions, observed_scenario_rates, scenario_profiles, scenario_run_detail
from .publish_benchmarks import cooperation_trials, PublishError


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
                self.assertEqual([card["score"] for card in cards],
                                 sorted((card["score"] for card in cards), reverse=True))
                for card in cards:
                    self.assertEqual(card["cost_complete"], card["cost_reported"] == card["trials"])
                    if card["cost_per_repair"] is not None:
                        self.assertAlmostEqual(card["cost_per_repair"], card["cost"] / card["passed"])
                    self.assertEqual(card["status"], "complete")
                    self.assertEqual(card["covered"], card["required"])
                    params = parse_qs(urlparse(card["evidence_url"]).query)
                    self.assertEqual(params["provider"], [provider])
                    self.assertEqual(params["model"], [card["model"]])
                    self.assertEqual(params["release"], [card["release"]])
                    self.assertEqual(params["harness"], [card["harness"]["id"]])
                    self.assertIn("reasoning", params)
                    for cell in card["cells"]:
                        link = parse_qs(urlparse(cell["evidence_url"]).query)
                        self.assertEqual(link["scenario"], [cell["scenario"]])
                        self.assertEqual(link["release"], [card["release"]])
                        self.assertEqual(link["model"], [card["model"]])
                        self.assertEqual(link["provider"], [provider])
                        self.assertIn(cell["outcome"], {"pass", "mixed", "fail", "unavailable"})
                        detail = report["scenario_runs"][cell["evidence_url"]]
                        self.assertEqual(len(detail["attempts"]), cell["trials"])
                        self.assertEqual(sum(a["passed"] for a in detail["attempts"]), cell["passed"])
                        raw = sorted((r for r in json.loads((root / "benchmark-data/releases" / (card["release"] + ".json")).read_text())["runs"]
                                      if r["model"] == card["model"] and (r.get("provider") or "unreported") == provider
                                      and (r.get("reasoning_effort") or "default") == card["reasoning"]
                                      and r["scenario"] == cell["scenario"] and r["scenario_version"] == cell["version"]),
                                     key=lambda r: r["attempt"])
                        self.assertEqual([a["failure"] for a in detail["attempts"]], [r.get("failure") for r in raw])
            cards = [card for group in cohort["groups"].values() for card in group]
            profile = cohort["scenario_profiles"][0]
            self.assertEqual(profile["configurations"], len(cards))
            for rate in profile["rates"]:
                self.assertEqual(rate["trials"], len(cards) * cohort["attempts"])
                self.assertEqual(rate["configurations"], len(cards))

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
        self.assertTrue(all(not profile["rates"] for cohort in public["cohorts"]
                            for profile in cohort["scenario_profiles"]))

    def test_observed_rates_include_unavailable_and_keep_scenario_versions(self):
        first = self.card()
        second = copy.deepcopy(first)
        second["cells"][0].update(passed=1, evaluated=2, unavailable=1)
        second["cells"][1]["version"] = 2
        rates = {(row["scenario"], row["version"]): row
                 for row in observed_scenario_rates([first, second])}
        self.assertAlmostEqual(rates[("a", 1)]["failure_rate"], 100 / 3)
        self.assertEqual(rates[("a", 1)]["unsuccessful"], 2)
        self.assertEqual(rates[("a", 1)]["trials"], 6)
        self.assertEqual(rates[("a", 1)]["unavailable"], 1)
        self.assertEqual(rates[("a", 1)]["configurations"], 2)
        self.assertEqual(rates[("b", 1)]["configurations"], 1)
        self.assertEqual(rates[("b", 2)]["configurations"], 1)
        self.assertEqual(first["score"], 50)

    def test_observed_profiles_follow_harness_and_preserve_release_entries(self):
        first = self.card()
        first.update(harness={"id": "claux"}, release="first")
        other = copy.deepcopy(first)
        other.update(harness={"id": "opencode"}, release="other")
        other["cells"][0]["passed"] = 0
        repeated = copy.deepcopy(first)
        repeated["release"] = "later"
        profiles = {profile["harness"]: profile for profile in scenario_profiles([first, other, repeated])}
        self.assertEqual(profiles[""]["configurations"], 3)
        self.assertEqual(profiles["claux"]["configurations"], 2)
        self.assertEqual(profiles["opencode"]["configurations"], 1)
        claux = {row["scenario"]: row for row in profiles["claux"]["rates"]}
        self.assertEqual(claux["a"]["failure_rate"], 0)
        self.assertEqual(claux["a"]["trials"], 6)
        self.assertEqual(profiles["opencode"]["rates"][0]["failure_rate"], 100)

    def test_run_details_keep_unknown_costs_and_whitelist_public_fields(self):
        runs = [
            {"attempt": 3, "reward": 0, "trial_status": "evaluated", "agent_duration_seconds": 10,
             "failure": "did not survive reboot", "failure_category": "database_pool_exhausted",
             "usage": {"cost_usd": .2}, "verification": {"immediate_http": True, "host_reboot": False},
             "transcript_file": "/private/transcript", "recording": {"model_rounds": 4, "tools": ["private"]}},
            {"attempt": 1, "reward": 1, "trial_status": "evaluated", "agent_duration_seconds": 0,
             "usage": {"cost_usd": 0}},
            {"attempt": 2, "reward": 1, "trial_status": "evaluated", "usage": None},
        ]
        detail = scenario_run_detail({"trials": 3, "passed": 2}, runs)
        self.assertEqual([a["attempt"] for a in detail["attempts"]], [1, 2, 3])
        self.assertEqual(detail["cost_reported"], 2)
        self.assertEqual(detail["known_cost_usd"], .2)
        self.assertEqual(detail["median_duration_seconds"], 5)
        self.assertTrue(detail["cost_lower_bound"])
        self.assertEqual(detail["attempts"][0]["usage"]["cost_usd"], 0)
        self.assertIsNone(detail["attempts"][1]["usage"]["cost_usd"])
        self.assertNotIn("private", json.dumps(detail))
        self.assertEqual(detail["attempts"][2]["verification"]["host_reboot"], False)

    def test_timeout_usage_is_a_lower_bound_even_when_reported(self):
        detail = scenario_run_detail({"trials": 1, "passed": 0}, [
            {"attempt": 1, "reward": 0, "trial_status": "evaluated",
             "failure_category": "agent_timeout", "usage": {"cost_usd": .1},
             "verification": {"after_agent_timeout": {"durable_repair": False}}},
        ])
        self.assertEqual(detail["cost_reported"], 1)
        self.assertTrue(detail["cost_lower_bound"])
        self.assertFalse(detail["attempts"][0]["verification"]["after_agent_timeout"]["durable_repair"])

    def test_empty_observed_sample_has_no_rates(self):
        self.assertEqual(observed_scenario_rates([]), [])
        self.assertEqual(scenario_profiles([]), [{"harness": "", "configurations": 0, "rates": []}])

    def test_frontier_suite_accepts_only_its_exact_pinned_contract(self):
        self.benchmark.id = "example-core-hard"
        self.benchmark.tier = "frontier"
        self.release["compatibility"]["benchmark_manifest"].update(id=self.benchmark.id,tier="frontier")
        self.assertEqual(self.card()["status"],"complete")
        self.assertEqual(self.card()["score"],50)
        self.release["compatibility"]["benchmark_manifest"]["sha256"] = "different"
        self.assertEqual(self.card()["status"],"provisional")

    def test_core_and_hard_contracts_cannot_be_interchanged_or_grouped(self):
        core = self.snapshot_release()
        hard = copy.deepcopy(core)
        hard["compatibility"]["benchmark_manifest"].update(id="example-core-hard",tier="frontier")
        self.assertNotEqual(condition_id(core),condition_id(hard))
        self.benchmark.tier = "frontier"
        with self.assertRaisesRegex(ValueError,"matching"):
            self.card()
        self.release["compatibility"]["benchmark_manifest"]["tier"] = "frontier"
        self.benchmark.id = "example-core-hard"
        with self.assertRaisesRegex(ValueError,"IDs differ"):
            self.card()

    def test_mixed_suite_page_keeps_labels_scores_and_observations_separate(self):
        from .benchmark_manifest import load_benchmark_manifest
        from .publish_benchmarks import render_site_template
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "benchmark-data"
            (data / "releases").mkdir(parents=True)
            cohorts = []
            versions = []
            for suite,tier,passed in (("core","core",3),("hard","frontier",0)):
                manifest = data / (suite+".toml")
                manifest.write_text(
                    'schema_version = 1\n[benchmark]\nid = "example-'+suite+'"\n'
                    'version = "1"\nstatus = "'+('experimental' if suite == 'hard' else 'stable')+'"\n'
                    'tier = "'+tier+'"\nattempts = 3\nagent_timeout_seconds = 900\n'
                    'required_host_harness_version = 23\n[pack]\npath = "."\nid = "example/pack"\n'
                    '[verification]\nrequire_immediate_recovery = true\nrequire_service_restart = true\n'
                    'require_host_reboot = true\npreserve_controller_state = true\n'
                    '[[scenarios]]\nid = "a"\nversion = 1\n[[scenarios]]\nid = "b"\nversion = 1\n')
                benchmark = load_benchmark_manifest(manifest)
                release = self.snapshot_release()
                release.update(version="20261029.0."+str(len(versions)),title=suite)
                release["compatibility"]["benchmark_manifest"].update(id=benchmark.id,tier=tier,sha256=benchmark.sha256)
                release["by_scenario_model"] = [self.row(name,passed,3) for name in ("a","b")]
                release["by_model"] = [{"provider":"test","model":"model","reasoning_effort":"high",
                                        "known_cost_usd":1,"cost_reported_trials":6,"median_duration_seconds":10}]
                release["runs"] = [{"provider":"test","model":"model","reasoning_effort":"high",
                                    "scenario":scenario,"scenario_version":1,"attempt":attempt,
                                    "reward":1 if passed else 0,"trial_status":"evaluated"}
                                   for scenario in ("a","b") for attempt in (1,2,3)]
                (data / "releases" / (release["version"]+".json")).write_text(json.dumps(release))
                versions.append(release["version"])
                cohorts.append({"release":release["version"],"manifest":str(manifest.relative_to(root)),
                                "label":"Core Hard" if suite == "hard" else "Core"})
            (data / "scores.json").write_text(json.dumps({"cohorts":cohorts}))
            report = build_scores(root,{"releases":versions})
            self.assertEqual(len(report["cohorts"]),2)
            core,hard = report["cohorts"]
            self.assertEqual(core["groups"]["test"][0]["score"],100)
            self.assertEqual(hard["groups"]["test"][0]["score"],0)
            self.assertNotEqual(core["id"],hard["id"])
            self.assertEqual(core["scenario_profiles"][0]["rates"][0]["failure_rate"],0)
            self.assertEqual(hard["scenario_profiles"][0]["rates"][0]["failure_rate"],100)
            self.assertEqual([group["label"] for group in report["scenario_details"]],["Core","Core Hard"])
            html = render_site_template("benchmark-scores.html",report=report)
            self.assertIn("Core Hard 1 · Experimental",html)
            self.assertIn("not directly comparable",html)
            self.assertEqual(html.count('class="score-cohort"'),2)

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
        self.assertEqual([c["score"] for c in grouped[0]["groups"]["openrouter"]], [75, 50])


class CooperationPublicationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        target=self.root/'benchmark-data/cooperation';target.parent.mkdir(parents=True)
        shutil.copytree(Path(__file__).resolve().parents[2]/'benchmark-data/cooperation',target)
        self.path=target/'trials.json'

    def write(self,value):self.path.write_text(json.dumps(value))

    def test_real_data_preserves_pair_failure_after_timeout(self):
        records=cooperation_trials(self.root)
        restricted=next(x for x in records if x['id']=='restricted-port')
        self.assertFalse(restricted['success'])
        self.assertTrue(all(restricted['milestones'][n]['cooperation'] for n in ('alpha','beta')))
        self.assertEqual(restricted['agent_exit_codes']['beta'],124)

    def test_shared_disk_result_has_no_peer_packets(self):
        record=next(x for x in cooperation_trials(self.root) if x['id']=='shared-disk')
        self.assertEqual(record['transport'],'shared-disk')
        self.assertEqual(record['evidence']['network_events'],[])
        self.assertTrue(all(record['milestones'][n]['shared_disk_changed'] for n in ('alpha','beta')))

    def test_free_for_all_records_unchanged_disk_and_success(self):
        record=next(x for x in cooperation_trials(self.root) if x['id']=='free-for-all')
        self.assertEqual(record['transport'],'free-for-all')
        self.assertTrue(record['success'])
        self.assertFalse(record['milestones']['alpha']['shared_disk_changed'])
        self.assertTrue(record['milestones']['alpha']['peer_payload_observed'])

    def test_rejects_reclassified_failure_and_changed_snapshot(self):
        data=json.loads(self.path.read_text());changed=copy.deepcopy(data)
        next(x for x in changed['trials'] if x['id']=='restricted-port')['success']=True
        self.write(changed)
        with self.assertRaises(PublishError):cooperation_trials(self.root)
        self.write(data)
        (self.root/'benchmark-data/cooperation/snapshots/restricted-port/lab/node.nix').write_text('tampered')
        with self.assertRaises(PublishError):cooperation_trials(self.root)


if __name__ == "__main__":
    unittest.main()
