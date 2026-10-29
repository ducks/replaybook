"""Complete scores for explicitly selected, isolated benchmark suites."""

from __future__ import annotations

from collections import defaultdict
import json
import hashlib
import statistics
from urllib.parse import urlencode

if __package__:
    from .benchmark_manifest import load_benchmark_manifest
else:
    from benchmark_manifest import load_benchmark_manifest



def score_release(release, benchmark):
    if __package__:
        from . import publish_benchmarks as publisher
    else:
        import publish_benchmarks as publisher
    compatibility = release["compatibility"]
    source = compatibility.get("benchmark_manifest", {})
    if benchmark.tier not in {"core", "frontier"} or source.get("tier") != benchmark.tier:
        raise ValueError("scores require matching, explicitly identified Core or frontier evidence")
    if source.get("id") != benchmark.id:
        raise ValueError("source and target benchmark IDs differ")
    exact_contract = (
        source.get("sha256") == benchmark.sha256
        and source.get("version") == benchmark.version
        and compatibility["attempts"] == benchmark.attempts
        and compatibility["agent_timeout_seconds"] == benchmark.agent_timeout_seconds
        and compatibility.get("harness_versions", [compatibility.get("harness_version")])
        == [benchmark.required_host_harness_version]
    )
    groups = defaultdict(dict)
    for row in release["by_scenario_model"]:
        key = (row.get("provider"), row["model"], row.get("reasoning_effort"))
        scenario = (row["scenario"], row["scenario_version"])
        if scenario in groups[key]:
            raise ValueError("duplicate scenario aggregate in lane")
        groups[key][scenario] = row
    cards = []
    for (provider, model, reasoning), rows in sorted(groups.items(), key=lambda item: str(item[0])):
        cells = []
        for expected in benchmark.scenarios:
            row = rows.get((expected.id, expected.version))
            cells.append({
                "scenario": expected.id, "version": expected.version,
                "passed": row["passed"] if row else 0,
                "evaluated": row["evaluated"] if row else 0,
                "trials": row["trials"] if row else 0,
                "unavailable": row["unavailable"] if row else 0,
                "complete": bool(row and row["trials"] == benchmark.attempts),
            })
        observed = [cell for cell in cells if cell["trials"]]
        evaluated_cells = [cell for cell in cells if cell["evaluated"]]
        score = 100 * statistics.mean(cell["passed"] / cell["trials"] for cell in observed) if observed else None
        repair_score = 100 * statistics.mean(cell["passed"] / cell["evaluated"] for cell in evaluated_cells) if evaluated_cells else None
        evaluated = sum(cell["evaluated"] for cell in cells)
        trials = sum(cell["trials"] for cell in cells)
        expected_keys = {(item.id, item.version) for item in benchmark.scenarios}
        complete = (
            exact_contract and set(rows) == expected_keys
            and all(cell["complete"] for cell in cells)
        )
        cards.append({
            "model": model, "label": publisher.label(release, "model", model),
            "provider": provider or "unreported", "reasoning": reasoning or "default",
            "score": score, "status": "complete" if complete else "provisional",
            "repair_score": repair_score, "repair_covered": len(evaluated_cells),
            "covered": len(observed), "required": len(cells),
            "complete_scenarios": sum(cell["complete"] for cell in cells),
            "evaluated": evaluated, "trials": trials,
            "unavailable": sum(cell["unavailable"] for cell in cells),
            "availability": 100 * evaluated / trials if trials else None,
            "cells": cells,
        })
    return {
        "schema_version": 2, "formula": "100 * mean(per-scenario passed / recorded trials)",
        "repair_formula": "100 * mean(per-scenario passed / evaluated)",
        "unavailable_policy": "All recorded unavailable attempts count as unsuccessful; no infrastructure exemptions inferred.",
        "release": release["version"], "benchmark": benchmark.id,
        "target_version": benchmark.version, "source_version": source.get("version"),
        "exact_contract": exact_contract, "attempts": benchmark.attempts,
        "harness": publisher.normalized_agent_harness(release), "cards": cards,
    }


def condition_id(release):
    """Comparison boundary, not an identity for pooling agent results."""
    c = release["compatibility"]
    snapshot = c.get("execution_snapshot") or {}
    scenarios = snapshot.get("selected_scenarios") or []
    boundary = {
        "manifest": c.get("benchmark_manifest"),
        "suite": c.get("suite"),
        "attempts": c.get("attempts"),
        "timeout": c.get("agent_timeout_seconds"),
        "host_versions": c.get("harness_versions", [c.get("harness_version")]),
        "controller": snapshot.get("host_harness_sha256"),
        "scenarios": sorted(scenarios, key=lambda s: (s["pack_id"], s["id"])),
    }
    # Unknown provenance must never accidentally join another release.
    if not boundary["controller"] or not scenarios or any(not s.get("sha256") for s in scenarios):
        boundary["isolated_release"] = release["version"]
    return hashlib.sha256(json.dumps(boundary, sort_keys=True).encode()).hexdigest()


def scenario_label(release, scenario):
    if label := release.get("scenario_labels", {}).get(scenario):
        return label
    words = scenario.split("-", 1)[-1].split("-")
    return " ".join(word.upper() if word in {"tls", "http", "fd", "api", "ca"} else word for word in words)


def observed_scenario_rates(cards):
    """Descriptive rates over complete, release-scoped configuration entries."""
    scenarios = {}
    for card in cards:
        for cell in card.get("cells", []):
            key = (cell["scenario"], cell["version"])
            if key not in scenarios:
                scenarios[key] = {
                    "scenario": cell["scenario"], "version": cell["version"],
                    "label": cell.get("label", cell["scenario"]),
                    "configurations": 0, "passed": 0, "trials": 0, "unavailable": 0,
                    "outcomes": [],
                }
            row = scenarios[key]
            row["configurations"] += 1
            row["outcomes"].append({
                "label": card.get("label", card.get("model", "Configuration")),
                "model": card.get("model"), "provider": card.get("provider"),
                "reasoning": card.get("reasoning"), "release": card.get("release"),
                "harness": card.get("harness", {}).get("label"),
                "passed": cell["passed"], "trials": cell["trials"],
                "unavailable": cell["unavailable"], "outcome": cell.get("outcome"),
                "evidence_url": cell.get("evidence_url"),
            })
            for field in ("passed", "trials", "unavailable"):
                row[field] += cell[field]
    rates = []
    for row in scenarios.values():
        row["unsuccessful"] = row["trials"] - row["passed"]
        row["failure_rate"] = 100 * row["unsuccessful"] / row["trials"] if row["trials"] else None
        rates.append(row)
    return sorted(rates, key=lambda row: (
        row["failure_rate"] is None, -(row["failure_rate"] or 0), row["scenario"], row["version"],
    ))


def scenario_profiles(cards):
    """Keep condition and harness selection aligned with the observed sample."""
    selections = {"": cards}
    for card in cards:
        harness = card.get("harness", {}).get("id")
        if harness:
            selections.setdefault(harness, []).append(card)
    return [
        {"harness": harness, "configurations": len(selected),
         "rates": observed_scenario_rates(selected)}
        for harness, selected in selections.items()
    ]


def group_conditions(cohorts):
    combined = {}
    for cohort in cohorts:
        key = cohort["id"]
        if key not in combined:
            combined[key] = {**cohort, "groups": defaultdict(list), "releases": [], "hidden": 0}
        group = combined[key]
        group["releases"].append(cohort["release"])
        group["hidden"] += cohort["hidden"]
        for provider, cards in cohort["groups"].items():
            group["groups"][provider].extend(cards)
    for group in combined.values():
        for cards in group["groups"].values():
            cards.sort(key=lambda card: (-card["score"], card.get("label", ""), card["release"]))
        group["scenario_profiles"] = scenario_profiles([
            card for cards in group["groups"].values() for card in cards
        ])
    return list(combined.values())


def scenario_run_detail(cell, runs):
    """Public attempt evidence for one exact release/provider/model/reasoning cell."""
    attempts = []
    for run in sorted(runs, key=lambda run: run["attempt"]):
        usage = run.get("usage") or {}
        recording = run.get("recording") or {}
        attempts.append({
            "attempt": run["attempt"], "passed": run["reward"] == 1,
            "status": run.get("trial_status"),
            "failure": run.get("failure"), "failure_category": run.get("failure_category"),
            "duration_seconds": run.get("agent_duration_seconds"),
            "timeout_seconds": run.get("agent_timeout_seconds"),
            "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
            "verification": run.get("verification"),
            "usage": {key: usage.get(key) for key in (
                "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "cost_source",
            )},
            "model_rounds": recording.get("model_rounds"), "tool_calls": recording.get("tool_calls"),
        })
    costs = [attempt["usage"]["cost_usd"] for attempt in attempts
             if attempt["usage"]["cost_usd"] is not None]
    durations = [attempt["duration_seconds"] for attempt in attempts
                 if attempt["duration_seconds"] is not None]
    return {
        "attempts": attempts, "recorded_attempts": len(attempts),
        "trials": cell["trials"], "passed": cell["passed"],
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "known_cost_usd": sum(costs) if costs else None, "cost_reported": len(costs),
        "cost_lower_bound": len(costs) < cell["trials"] or any(
            attempt["failure_category"] == "agent_timeout" for attempt in attempts
        ),
    }


def build_scores(root, index):
    """Explicitly selected releases and frozen contracts; never pick a best run."""
    config_path = root / "benchmark-data/scores.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {"cohorts": []}
    cohorts = []
    scenario_runs = {}
    seen = set()
    for entry in config["cohorts"]:
        version = entry["release"]
        if version in seen:
            raise ValueError(f"duplicate score release: {version}")
        seen.add(version)
        if version not in index["releases"]:
            raise ValueError(f"score release is not published: {version}")
        release = json.loads((root / "benchmark-data/releases" / f"{version}.json").read_text())
        benchmark = load_benchmark_manifest(root / entry["manifest"])
        report = score_release(release, benchmark)
        indexed_runs = defaultdict(list)
        for run in release.get("runs", []):
            indexed_runs[(run.get("provider") or "unreported", run["model"],
                          run.get("reasoning_effort") or "default", run["scenario"],
                          run["scenario_version"])].append(run)
        groups = defaultdict(list)
        hidden = 0
        for card in report["cards"]:
            if card["status"] != "complete":
                hidden += 1
                continue
            aggregate = next(row for row in release["by_model"]
                             if row["model"] == card["model"]
                             and (row.get("provider") or "unreported") == card["provider"]
                             and (row.get("reasoning_effort") or "default") == card["reasoning"])
            card["cost"] = aggregate.get("known_cost_usd")
            card["cost_reported"] = aggregate.get("cost_reported_trials", 0)
            if not card["cost_reported"]:
                card["cost"] = None
            card["cost_complete"] = card["cost_reported"] == card["trials"]
            card["duration"] = aggregate.get("median_duration_seconds")
            card["passed"] = sum(cell["passed"] for cell in card["cells"])
            card["cost_per_repair"] = card["cost"] / card["passed"] if card["cost"] is not None and card["passed"] else None
            card["release"] = version
            card["harness"] = report["harness"]
            card["evidence_url"] = "benchmark-evidence.html?" + urlencode({
                "release": version, "provider": card["provider"],
                "harness": report["harness"]["id"], "model": card["model"],
                "reasoning": card["reasoning"] if card["reasoning"] != "default" else "Unspecified",
            })
            for cell in card["cells"]:
                cell["label"] = scenario_label(release, cell["scenario"])
                cell["short_id"] = cell["scenario"].split("-", 1)[0]
                cell["evidence_url"] = card["evidence_url"] + "&" + urlencode({"scenario": cell["scenario"]})
                cell["outcome"] = (
                    "unavailable" if cell["unavailable"] else
                    "pass" if cell["passed"] == cell["trials"] else
                    "fail" if cell["passed"] == 0 else "mixed"
                )
                matching_runs = indexed_runs[(card["provider"], card["model"], card["reasoning"],
                                               cell["scenario"], cell["version"])]
                scenario_runs[cell["evidence_url"]] = scenario_run_detail(cell, matching_runs)
            groups[card["provider"]].append(card)
        cohorts.append({
            "id": condition_id(release),
            "release": version, "benchmark": benchmark.version,
            "benchmark_id": benchmark.id, "tier": benchmark.tier,
            "label": entry.get("label") or ("Core" if benchmark.tier == "core" else "Frontier"),
            "experimental": benchmark.status == "experimental",
            "title": release["title"], "harness": report["harness"],
            "attempts": benchmark.attempts, "timeout": benchmark.agent_timeout_seconds,
            "groups": dict(groups), "hidden": hidden,
        })
    grouped = group_conditions(cohorts)
    return {
        "cohorts": grouped,
        "scenario_runs": scenario_runs,
        "scenario_details": [{"condition": group["id"], "benchmark": group["benchmark"], "label": group["label"], "profiles": group["scenario_profiles"]}
                             for group in grouped],
    }
