#!/usr/bin/env python3
"""Create, validate, and submit portable Replaybook benchmark evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

if __package__:
    from integrations.host.publish_benchmarks import (
        PublishError,
        aggregate_runs,
        create_release_from_sources,
        import_summary,
        read_json,
        store_release,
        validate_compatible,
        validate_source_matrix,
        write_json,
    )
else:
    from publish_benchmarks import (
        PublishError,
        aggregate_runs,
        create_release_from_sources,
        import_summary,
        read_json,
        store_release,
        validate_compatible,
        validate_source_matrix,
        write_json,
    )


REPO_DIR = Path(__file__).resolve().parents[2]
SUBMISSIONS_DIR = Path("benchmark-submissions")
BUNDLE_KIND = "replaybook-benchmark-submission"
BUNDLE_SCHEMA_VERSION = 1
SUBMISSION_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w:])/(?:home|Users|tmp|var/tmp)/[^\s'\"<>]+|[A-Za-z]:\\[^\s'\"<>]+"
)
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class SubmissionError(ValueError):
    """A submission bundle or submission operation is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def submission_id(evidence: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(evidence)).hexdigest()


def sanitize_text(value: str) -> str:
    return ABSOLUTE_PATH_PATTERN.sub("<redacted-path>", value)


def sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_value(item) for key, item in value.items()}
    return value


def create_bundle(
    summaries: Sequence[Path], *, submitter: str | None = None
) -> dict[str, Any]:
    if not summaries:
        raise SubmissionError("at least one matrix summary is required")
    sources = []
    for index, summary in enumerate(summaries, start=1):
        source = sanitize_value(import_summary(summary))
        source["source"] = f"matrix-{index:03d}"
        sources.append(source)
    compatibility = validate_compatible(sources)
    runs = [run for source in sources for run in source["runs"]]
    evidence = {
        "compatibility": compatibility,
        "sources": sources,
        "totals": aggregate_runs(runs),
    }
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "kind": BUNDLE_KIND,
        "submission_id": submission_id(evidence),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence": evidence,
    }
    if submitter:
        bundle["submitter"] = submitter
    validate_bundle(bundle, Path("<generated-bundle>"))
    return bundle


def read_bundle(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SubmissionError(f"could not read submission bundle {path}: {error}") from error
    if not isinstance(value, dict):
        raise SubmissionError(f"{path}: submission bundle must be an object")
    return value


def reject_private_paths(value: Any, path: Path) -> None:
    if isinstance(value, str) and ABSOLUTE_PATH_PATTERN.search(value):
        raise SubmissionError(f"{path}: submission contains an absolute local path")
    if isinstance(value, list):
        for item in value:
            reject_private_paths(item, path)
    elif isinstance(value, dict):
        for item in value.values():
            reject_private_paths(item, path)


def validate_bundle(bundle: dict[str, Any], path: Path) -> None:
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise SubmissionError(f"{path}: unsupported submission schema version")
    if bundle.get("kind") != BUNDLE_KIND:
        raise SubmissionError(f"{path}: invalid submission kind")
    identifier = bundle.get("submission_id")
    if not isinstance(identifier, str) or not SUBMISSION_ID_PATTERN.fullmatch(identifier):
        raise SubmissionError(f"{path}: invalid submission_id")
    created_at = bundle.get("created_at")
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise SubmissionError(f"{path}: created_at must be a UTC timestamp")
    submitter = bundle.get("submitter")
    if submitter is not None and (not isinstance(submitter, str) or not submitter.strip()):
        raise SubmissionError(f"{path}: submitter must be a non-empty string")
    evidence = bundle.get("evidence")
    if not isinstance(evidence, dict):
        raise SubmissionError(f"{path}: evidence must be an object")
    if identifier != submission_id(evidence):
        raise SubmissionError(f"{path}: submission_id does not match the evidence")
    sources = evidence.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SubmissionError(f"{path}: evidence sources must be a non-empty array")
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise SubmissionError(f"{path}: evidence source {index} must be an object")
        try:
            validate_source_matrix(source, Path(f"{path}:source-{index}"))
        except PublishError as error:
            raise SubmissionError(str(error)) from error
    try:
        compatibility = validate_compatible(sources)
    except PublishError as error:
        raise SubmissionError(str(error)) from error
    if evidence.get("compatibility") != compatibility:
        raise SubmissionError(f"{path}: compatibility metadata does not match sources")
    runs = [run for source in sources for run in source["runs"]]
    if evidence.get("totals") != aggregate_runs(runs):
        raise SubmissionError(f"{path}: aggregate totals do not match submitted runs")
    reject_private_paths(bundle, path)


def validate_path(path: Path) -> dict[str, Any]:
    bundle = read_bundle(path)
    validate_bundle(bundle, path)
    return bundle


def validate_directory(directory: Path) -> int:
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    for path in paths:
        bundle = validate_path(path)
        if path.stem != bundle["submission_id"]:
            raise SubmissionError(
                f"{path}: filename must match the content-addressed submission_id"
            )
    return len(paths)


def command_output(
    command: Sequence[str], *, cwd: Path, run: RunCommand = subprocess.run
) -> str:
    try:
        completed = run(
            list(command), cwd=cwd, check=True, text=True, capture_output=True
        )
    except FileNotFoundError as error:
        raise SubmissionError(f"required command is not installed: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise SubmissionError(f"{' '.join(command)} failed: {detail}") from error
    return completed.stdout.strip()


def require_clean_checkout(root: Path, *, run: RunCommand = subprocess.run) -> None:
    if command_output(["git", "status", "--porcelain"], cwd=root, run=run):
        raise SubmissionError("benchmark submission requires a clean Git checkout")


def submit_bundle(
    bundle_path: Path,
    *,
    root: Path,
    target_repo: str,
    base_branch: str,
    run: RunCommand = subprocess.run,
) -> str:
    bundle = validate_path(bundle_path)
    require_clean_checkout(root, run=run)
    current_branch = command_output(
        ["git", "branch", "--show-current"], cwd=root, run=run
    )
    if current_branch != base_branch:
        raise SubmissionError(
            f"benchmark submission must start from the {base_branch} branch"
        )
    identifier = bundle["submission_id"]
    short_id = identifier[:12]
    branch = f"benchmark-submission/{short_id}"
    destination = root / SUBMISSIONS_DIR / f"{identifier}.json"
    if destination.exists():
        raise SubmissionError(f"submission already exists: {destination}")

    login = command_output(["gh", "api", "user", "--jq", ".login"], cwd=root, run=run)
    target_owner = target_repo.split("/", 1)[0]
    remote = "origin"
    if login != target_owner:
        remote = "benchmark-fork"
        remotes = command_output(["git", "remote"], cwd=root, run=run).splitlines()
        if remote not in remotes:
            command_output(
                [
                    "gh",
                    "repo",
                    "fork",
                    target_repo,
                    "--clone=false",
                    "--remote",
                    "--remote-name",
                    remote,
                ],
                cwd=root,
                run=run,
            )

    command_output(["git", "switch", "-c", branch], cwd=root, run=run)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle_path.resolve(), destination)
    command_output(["git", "add", str(destination.relative_to(root))], cwd=root, run=run)
    command_output(
        ["git", "commit", "-m", f"Submit benchmark evidence {short_id}"],
        cwd=root,
        run=run,
    )
    command_output(["git", "push", "-u", remote, branch], cwd=root, run=run)
    title = f"Benchmark submission {short_id}"
    body = (
        f"Replaybook benchmark evidence bundle `{identifier}`.\n\n"
        "The benchmark-submission workflow validates its content digest, "
        "matrix completeness, frozen execution metadata, compatibility boundary, "
        "aggregates, and absence of local paths."
    )
    return command_output(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            target_repo,
            "--base",
            base_branch,
            "--head",
            f"{login}:{branch}",
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=root,
        run=run,
    )


def accept_bundle(
    bundle_path: Path,
    *,
    version: str,
    root: Path,
    annotations_path: Path | None = None,
) -> dict[str, Any]:
    """Promote a reviewed submission bundle into a public benchmark release."""
    bundle = validate_path(bundle_path)
    annotations = read_json(annotations_path) if annotations_path else {}
    release = create_release_from_sources(
        version, bundle["evidence"]["sources"], annotations
    )
    release["submission_id"] = bundle["submission_id"]
    release["submitted_by"] = bundle.get("submitter")
    store_release(root, release)
    return release


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repo-dir", type=Path, default=REPO_DIR)
    commands = value.add_subparsers(dest="command", required=True)

    bundle = commands.add_parser("bundle", help="create a sanitized evidence bundle")
    bundle.add_argument("--output", required=True, type=Path)
    bundle.add_argument("--submitter")
    bundle.add_argument("summaries", nargs="+", type=Path)

    validate = commands.add_parser("validate", help="validate evidence bundles")
    validate.add_argument("bundles", nargs="+", type=Path)

    validate_all = commands.add_parser(
        "validate-all", help="validate all JSON bundles in a directory"
    )
    validate_all.add_argument("directory", type=Path, default=SUBMISSIONS_DIR, nargs="?")

    submit = commands.add_parser("submit", help="open a GitHub pull request")
    submit.add_argument("bundle", type=Path)
    submit.add_argument("--target-repo", default="ducks/replaybook")
    submit.add_argument("--base", default="main")

    accept = commands.add_parser(
        "accept", help="promote a reviewed bundle to a dated benchmark release"
    )
    accept.add_argument("bundle", type=Path)
    accept.add_argument("--version", required=True)
    accept.add_argument("--annotations", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    root = args.repo_dir.resolve()
    try:
        if args.command == "bundle":
            output = args.output.resolve()
            write_json(output, create_bundle(args.summaries, submitter=args.submitter))
            print(output)
        elif args.command == "validate":
            for path in args.bundles:
                validate_path(path)
                print(f"valid: {path}")
        elif args.command == "validate-all":
            count = validate_directory(args.directory)
            print(f"validated {count} benchmark submission bundle(s)")
        elif args.command == "submit":
            print(
                submit_bundle(
                    args.bundle,
                    root=root,
                    target_repo=args.target_repo,
                    base_branch=args.base,
                )
            )
        else:
            release = accept_bundle(
                args.bundle,
                version=args.version,
                root=root,
                annotations_path=args.annotations,
            )
            print(f"accepted {release['submission_id']} as {release['version']}")
    except (PublishError, SubmissionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
