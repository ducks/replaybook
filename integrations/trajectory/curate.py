#!/usr/bin/env python3
"""Local review and versioned ATIF datasets. No training or publication is performed."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.trajectory.export_atif import load_object, resolve_trials, to_atif, ATIF_VERSION


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w") as target:
            target.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)



def checked_object(path, expected=None):
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if expected is not None and sha != expected:
        raise ValueError("artifact changed since review or inventory")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("unsupported transcript or review object")
    return value, sha


def observable_hash(trajectory):
    steps = []
    for step in trajectory["steps"]:
        steps.append({"source":step.get("source"), "message":step.get("message"),
            "calls":[{"name":call.get("function_name"),"arguments":call.get("arguments")} for call in step.get("tool_calls",[])],
            "results":[{"content":item.get("content"),"is_error":item.get("extra",{}).get("is_error")} for item in step.get("observation",{}).get("results",[])]})
    return hashlib.sha256(encoded(steps).encode()).hexdigest()


def inventory(inputs, output):
    if output.exists():
        raise ValueError("inventory already exists; create a new inventory to preserve reviews")
    records = {}
    unsupported = []
    for result_path, transcript_path in resolve_trials(inputs):
        result, result_sha = checked_object(result_path)
        try:
            transcript, transcript_sha = checked_object(transcript_path)
            to_atif(result, transcript, result_path)
        except ValueError:
            unsupported.append({"result_path":str(result_path), "reason":"unsupported transcript format"})
            continue
        hashes = {"result":result_sha, "transcript":transcript_sha}
        identifier = hashlib.sha256(encoded(hashes).encode()).hexdigest()
        verification = result.get("verification") or {}
        records[identifier] = {"id":identifier, "result_path":str(result_path), "transcript_path":str(transcript_path),
            "source_sha256":hashes, "scenario":result.get("scenario"), "scenario_version":result.get("scenario_version"),
            "pack":result.get("scenario_pack"), "model":result.get("model"), "provider":result.get("provider"),
            "agent":result.get("agent"), "reward":result.get("reward"), "trial_status":result.get("trial_status"),
            "failure_category":result.get("failure_category"), "verification":verification,
            "verified_success":result.get("reward") == 1 and result.get("trial_status") == "evaluated" and all(verification.get(key) is True for key in ("immediate_http", "service_restart", "host_reboot")),
            "family":None, "quality":"unreviewed", "privacy":"unreviewed", "training_rights":"unknown", "tags":[]}
    value = {"schema_version":1, "records":list(records.values()), "unsupported":unsupported}
    write(output, value)
    return value


def sources(record):
    result, transcript = Path(record["result_path"]), Path(record["transcript_path"])
    result_data, _ = checked_object(result, record["source_sha256"]["result"])
    transcript_data, _ = checked_object(transcript, record["source_sha256"]["transcript"])
    return to_atif(result_data, transcript_data, result)



def find(manifest, identifier):
    matches = [record for record in manifest["records"] if record["id"].startswith(identifier)]
    if len(matches) != 1:
        raise ValueError("ID must resolve to exactly one record")
    return matches[0]


def prepare(manifest, identifier, output):
    if output.exists():
        raise ValueError("review copy already exists")
    write(output, sources(find(manifest, identifier)))


def validate_copy(record, trajectory, original):
    if trajectory.get("schema_version") != ATIF_VERSION or not isinstance(trajectory.get("steps"), list):
        raise ValueError("review copy must be an ATIF trajectory")
    # Reviewers can redact/trim observable steps, not substitute a different
    # model, incident, or verifier result and retain the original provenance.
    for key in ("session_id", "agent"):
        if trajectory.get(key) != original.get(key):
            raise ValueError("review copy changed protected identity")
    def evaluation(value):
        return value.get("final_metrics", {}).get("extra", {}).get("infrastructure_evaluation")
    if evaluation(trajectory) != evaluation(original):
        raise ValueError("review copy changed protected evaluation metadata")
    outcome = evaluation(original)["outcome"]
    verified = outcome.get("reward") == 1 and outcome.get("status") == "evaluated" and all((outcome.get("verification") or {}).get(key) is True for key in ("immediate_http", "service_restart", "host_reboot"))
    if not trajectory["steps"] or not record["verified_success"] or not verified:
        raise ValueError("training examples require a lifecycle-verified success and observable steps")
    if not any(step.get("tool_calls") for step in trajectory["steps"]):
        raise ValueError("review copy contains no tool calls")


def review(path, identifier, *, family, reviewer, quality, privacy, rights, evidence, copy=None, tags=(), notes=""):
    manifest = load_object(path)
    record = find(manifest, identifier)
    original = sources(record)
    if not family.strip() or not reviewer.strip():
        raise ValueError("family and reviewer are required")
    if rights == "approved" and not evidence.strip():
        raise ValueError("approved training rights require recorded evidence")
    if quality == "accepted":
        if copy is None:
            raise ValueError("accepted examples require an explicit reviewed copy")
        trajectory, copy_sha = checked_object(copy)
        validate_copy(record, trajectory, original)
    record.update(family=family.strip(), reviewer=reviewer.strip(), quality=quality, privacy=privacy,
        training_rights=rights, rights_evidence=evidence, tags=sorted(set(tags)), notes=notes,
        reviewed_at=datetime.now(timezone.utc).isoformat())
    if copy is not None:
        record.update(reviewed_copy=str(copy.resolve()), reviewed_copy_sha256=checked_object(copy)[1] if quality != "accepted" else copy_sha)
    write(path, manifest)


def eligible(record):
    return record.get("verified_success") is True and record.get("quality") == "accepted" and record.get("privacy") == "approved" and record.get("training_rights") == "approved" and bool(record.get("family")) and bool(record.get("rights_evidence"))


def export(manifest, splits, output, version):
    if output.exists():
        raise ValueError("dataset destination already exists; datasets are immutable")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", version):
        raise ValueError("invalid dataset version")
    sets = {key:set(splits.get(key, [])) for key in ("train", "validation", "test")}
    if any(not isinstance(splits.get(key), list) or any(not isinstance(x, str) or not x for x in splits[key]) for key in sets):
        raise ValueError("split plan requires explicit train, validation and test family lists")
    if any(sets[a] & sets[b] for a,b in (("train","test"),("train","validation"),("validation","test"))):
        raise ValueError("incident families must not cross dataset splits")
    selected = [record for record in manifest["records"] if eligible(record)]
    if not selected:
        raise ValueError("no reviewed, privacy-approved, rights-approved verified examples")
    selected_families = {record["family"] for record in selected}
    if (sets["train"] | sets["validation"] | sets["test"]) - selected_families:
        raise ValueError("split plan names a family with no eligible examples")
    output.parent.mkdir(parents=True, exist_ok=True)
    seen_content = {}
    scenario_splits = {}
    rows = {key:[] for key in sets}
    provenance = []
    for record in sorted(selected, key=lambda x:x["id"]):
        original = sources(record)
        copy = Path(record["reviewed_copy"])
        trajectory, _ = checked_object(copy, record["reviewed_copy_sha256"])
        validate_copy(record, trajectory, original)
        matches = [key for key in sets if record["family"] in sets[key]]
        if len(matches) != 1:
            raise ValueError("every selected family requires exactly one explicit split")
        split = matches[0]
        task = original["final_metrics"]["extra"]["infrastructure_evaluation"]["task"]
        scenario_key = ((task.get("pack") or {}).get("id"), task["id"])
        if scenario_key in scenario_splits and scenario_splits[scenario_key] != split:
            raise ValueError("versions and models of the same scenario must not cross splits")
        scenario_splits[scenario_key] = split
        # Exact observable duplicates may not cross splits even if mislabelled.
        content_hash = observable_hash(trajectory)
        if content_hash in seen_content and seen_content[content_hash] != split:
            raise ValueError("duplicate observable trajectory crosses splits")
        seen_content[content_hash] = split
        rows[split].append({"id":record["id"], "family":record["family"], "tags":record.get("tags",[]), "trajectory":trajectory})
        provenance.append({"id":record["id"], "source_sha256":record["source_sha256"], "reviewed_copy_sha256":record["reviewed_copy_sha256"], "family":record["family"], "split":split, "model":record["model"], "provider":record["provider"], "rights_evidence":record["rights_evidence"]})
    with tempfile.TemporaryDirectory(prefix=".dataset-", dir=output.parent) as temporary:
        staged = Path(temporary) / "dataset"
        staged.mkdir()
        for split, items in rows.items():
            (staged / (split + ".jsonl")).write_text("".join(encoded(item)+"\n" for item in items))
        metadata = {"schema_version":1, "version":version, "format":ATIF_VERSION, "splits":{key:len(value) for key,value in rows.items()}, "families":{key:sorted(value) for key,value in sets.items()}, "files":{p.name:digest(p) for p in sorted(staged.glob("*.jsonl"))}, "provenance":provenance,
            "limitations":["Human review and rights evidence are assertions requiring independent validation.", "Family grouping reduces leakage; it does not detect semantic near-duplicates or prove generalization.", "ATIF export is not a model-specific fine-tuning recipe. No training or publication has occurred."]}
        write(staged / "manifest.json", metadata)
        staged.rename(output)
    return metadata


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index"); index.add_argument("inputs", nargs="+", type=Path); index.add_argument("--output", type=Path, required=True)
    view = commands.add_parser("summary"); view.add_argument("manifest", type=Path)
    prep = commands.add_parser("prepare"); prep.add_argument("manifest", type=Path); prep.add_argument("id"); prep.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("review"); check.add_argument("manifest", type=Path); check.add_argument("id"); check.add_argument("--family", required=True); check.add_argument("--reviewer", required=True); check.add_argument("--quality", choices=["accepted","rejected"], required=True); check.add_argument("--privacy", choices=["unreviewed","approved","rejected"], default="unreviewed"); check.add_argument("--rights", choices=["unknown","approved","rejected"], default="unknown"); check.add_argument("--rights-evidence", default=""); check.add_argument("--copy", type=Path); check.add_argument("--tag", action="append", default=[]); check.add_argument("--notes", default="")
    out = commands.add_parser("export"); out.add_argument("manifest", type=Path); out.add_argument("--splits", type=Path, required=True); out.add_argument("--output", type=Path, required=True); out.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if args.command == "index":
            value = inventory(args.inputs, args.output)
            print(json.dumps({"records":len(value["records"]),"unsupported":len(value["unsupported"])}))
        elif args.command == "summary":
            records = load_object(args.manifest)["records"]
            print(json.dumps({"records":len(records), "scenarios":len({r["scenario"] for r in records}), "models":len({r["model"] for r in records}), "verified_successes":sum(r["verified_success"] for r in records), "training_eligible":sum(eligible(r) for r in records), "failures":dict(Counter(r["failure_category"] for r in records if r["failure_category"]))},indent=2))
        elif args.command == "prepare": prepare(load_object(args.manifest), args.id, args.output)
        elif args.command == "review": review(args.manifest,args.id,family=args.family,reviewer=args.reviewer,quality=args.quality,privacy=args.privacy,rights=args.rights,evidence=args.rights_evidence,copy=args.copy,tags=args.tag,notes=args.notes)
        else: print(json.dumps(export(load_object(args.manifest),load_object(args.splits),args.output,args.version)["splits"]))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"curation failed: {error}",file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__": raise SystemExit(main())
