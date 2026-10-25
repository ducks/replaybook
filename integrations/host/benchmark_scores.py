"""Complete Core scores for explicitly selected, isolated release cohorts."""

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
    if benchmark.tier != "core" or source.get("tier") != "core":
        raise ValueError("this spike only scores explicitly identified Core evidence")
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
    return list(combined.values())


def build_scores(root, index):
    """Explicitly selected releases and frozen contracts; never pick a best run."""
    config_path = root / "benchmark-data/scores.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {"cohorts": []}
    cohorts = []
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
            groups[card["provider"]].append(card)
        cohorts.append({
            "id": condition_id(release),
            "release": version, "benchmark": benchmark.version,
            "title": release["title"], "harness": report["harness"],
            "attempts": benchmark.attempts, "timeout": benchmark.agent_timeout_seconds,
            "groups": dict(groups), "hidden": hidden,
        })
    return {"cohorts": group_conditions(cohorts)}
