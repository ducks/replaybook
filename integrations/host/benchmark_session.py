"""Start detached benchmark sessions and inspect authoritative matrix artifacts.

Profiles are operator configuration. This module contains no model catalog,
credentials, scoring rules, or replacement matrix execution engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Any

from integrations.host.run_host_matrix import (
    REPO_DIR, build_jobs, completed_worker, parse_args as matrix_args,
    summarize_runs, utc_now, write_json_atomic,
)

PUBLIC_ENV = {
    "REPLAYBOOK_CLAUX_AUTH_BINARY", "REPLAYBOOK_CLAUX_PROVIDER_ROUTES",
    "REPLAYBOOK_OPENAI_UPSTREAM", "REPLAYBOOK_OPENAI_PROXY_PATH",
}
TERMINAL_STATES = {"completed", "incomplete", "failed", "interrupted"}


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "replaybook/sessions.json"


def load_profile(path: Path, name: str) -> dict[str, Any]:
    config = read_object(path.expanduser())
    if config.get("schema_version") != 1:
        raise ValueError("unsupported session configuration schema")
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("session configuration profiles must be an object")
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        raise ValueError(f"profile {name!r} is not defined in {path}")
    args = profile.get("args")
    if not isinstance(args, list) or not args or not all(isinstance(x, str) for x in args):
        raise ValueError("profile args must be a non-empty array of matrix arguments")
    try:
        parsed = matrix_args(args)
    except SystemExit as error:
        raise ValueError("profile contains invalid matrix arguments") from error
    if any((parsed.resume, parsed.output_dir, parsed.check, parsed.list_scenarios,
            parsed.refresh_controller, parsed.retry_unavailable)):
        raise ValueError("session profiles cannot override lifecycle or output arguments")
    environment = profile.get("environment", {})
    if not isinstance(environment, dict) or any(
        key not in PUBLIC_ENV or not isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError("profile environment accepts only public transport settings; keep credentials in the shell or auth store")
    return {"args": args, "environment": environment}


def worker_identity(pid: int) -> str | None:
    """Include process start time so PID reuse cannot make an old session live."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text.rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, IndexError):
        return None


def worker_alive(session: Path) -> bool:
    try:
        worker = read_object(session / "worker.json")
        identity = worker_identity(worker["pid"])
        return identity is not None and identity == worker.get("identity")
    except (ValueError, KeyError, TypeError):
        return False


def resolve_target(value: str, jobs: Path) -> Path:
    if value != "latest":
        path = Path(value).expanduser()
        candidates = [path, jobs / "sessions" / value, jobs / value]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate.resolve()
        raise ValueError(f"session or matrix not found: {value}")
    sessions = [
        path.parent / "worker.json" if (path.parent / "worker.json").exists() else path
        for path in (jobs / "sessions").glob("*/session.json")
    ]
    matrices = list(jobs.glob("host-matrix-*/benchmark.json"))
    choices = sessions + matrices
    if not choices:
        raise ValueError(f"no benchmark sessions or matrices under {jobs}")
    return max(choices, key=lambda p: p.stat().st_mtime).parent.resolve()


def matrix_report(matrix: Path, running: bool = False) -> dict[str, Any]:
    benchmark = read_object(matrix / "benchmark.json")
    scenarios = [item["id"] for item in benchmark["scenarios"]]
    jobs = build_jobs(
        scenarios=scenarios,
        models=[None if model == "oracle" else model for model in benchmark["models"]],
        attempts=benchmark["attempts"], base_port=benchmark["base_port"],
        matrix_dir=matrix,
        reasoning_efforts=benchmark.get("reasoning_efforts") or [None],
    )
    runs = []
    active = []
    invalid = []
    for job in jobs:
        worker = completed_worker(job, (benchmark.get("agent") or {}).get("provider"))
        if worker is not None:
            runs.append(worker.result)
        elif (job.output_dir / "result.json").exists():
            invalid.append(job.run_id)
        elif job.log_file.exists():
            age = max(0, int(time.time() - job.log_file.stat().st_mtime))
            active.append({"run_id": job.run_id, "last_log_update_seconds": age})
    totals = summarize_runs(runs)
    summary_path = matrix / "summary.json"
    infrastructure_errors = []
    summary = None
    if summary_path.exists() and not running:
        try:
            summary = read_object(summary_path)
        except ValueError:
            # The matrix writes this artifact at completion. A watcher can
            # observe the file before that write has finished.
            pass
    if summary is not None:
        infrastructure_errors = summary.get("infrastructure_errors") or []
        complete = len(runs) == len(jobs) and not totals["unavailable"] and not infrastructure_errors and not invalid
        state = "completed" if complete else "incomplete"
        active = []
    else:
        state = "in_progress"
    return {
        "matrix": str(matrix), "state": state, "started_at": benchmark.get("started_at"),
        "expected": len(jobs), "completed": len(runs), "totals": totals,
        "failure_categories": dict(Counter(run.get("failure_category") for run in runs if run.get("failure_category"))),
        "active": active, "invalid_results": invalid,
        "infrastructure_errors": infrastructure_errors,
    }


def session_report(target: Path) -> dict[str, Any]:
    if not (target / "session.json").exists():
        return {**matrix_report(target), "managed": False}
    spec = read_object(target / "session.json")
    state = read_object(target / "status.json") if (target / "status.json").exists() else {"state": "starting"}
    alive = worker_alive(target)
    matrix = target / "matrix"
    report = matrix_report(matrix, running=alive and state["state"] == "running") if (matrix / "benchmark.json").exists() else {
        "matrix": str(matrix), "expected": None, "completed": 0,
        "totals": summarize_runs([]), "active": [], "failure_categories": {},
    }
    # A stale terminal summary can exist briefly while a resumed run works.
    if alive and state["state"] == "running":
        report["state"] = "running"
    elif state["state"] in TERMINAL_STATES:
        report["state"] = state["state"]
    elif not alive:
        if report.get("state") not in {"completed", "incomplete"}:
            report["state"] = "interrupted"
        report["active"] = []
    else:
        report["state"] = state["state"]
    return {
        **report, "managed": True, "session": str(target), "profile": spec["profile"],
        "worker_alive": alive, "after": spec.get("after"),
        "log": str(target / "controller.log"), "detail": state.get("detail"),
    }


def launch_worker(session: Path, resume: bool = False, retry_unavailable: bool = False,
                  agent_env_file: Path | None = None) -> int:
    with (session / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if worker_alive(session):
            raise ValueError("this session already has a live worker")
        if resume and not (session / "matrix/benchmark.json").exists():
            raise ValueError("no matrix to resume; start a new session")
        state = "resuming" if resume else "starting"
        write_json_atomic(session / "status.json", {"state": state, "updated_at": utc_now()})
        command = [sys.executable, str(REPO_DIR / "replaybook-bench"), "_run", str(session)]
        if resume:
            command.append("--resume")
        if retry_unavailable:
            command.append("--retry-unavailable")
        if agent_env_file is not None:
            command.extend(["--agent-env-file", str(agent_env_file.expanduser().resolve())])
        # Spawn directly without a Popen object that expects its caller to wait.
        # The absolute entry point makes this independent of the caller's cwd.
        with open(os.devnull, "rb") as source, (session / "controller.log").open("ab") as log:
            pid = os.posix_spawn(sys.executable, command, dict(os.environ), setsid=True,
                                 file_actions=[
                                     (os.POSIX_SPAWN_DUP2, source.fileno(), 0),
                                     (os.POSIX_SPAWN_DUP2, log.fileno(), 1),
                                     (os.POSIX_SPAWN_DUP2, log.fileno(), 2),
                                 ])
        write_json_atomic(session / "worker.json", {"pid": pid, "identity": worker_identity(pid)})
        return pid


def start_session(profile: str, config: Path, jobs: Path, after: str | None = None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", profile):
        raise ValueError("profile name must use letters, digits, dots, underscores, or hyphens")
    settings = load_profile(config, profile)
    dependency = resolve_target(after, jobs) if after else None
    if dependency and not any((dependency / name).exists() for name in ("session.json", "benchmark.json")):
        raise ValueError("dependency is not a session or matrix")
    name = f"{utc_now().replace(':', '-').replace('.', '-')}-{profile}-{secrets.token_hex(3)}"
    session = (jobs / "sessions" / name).resolve()
    session.mkdir(parents=True)
    write_json_atomic(session / "session.json", {
        "schema_version": 1, "profile": profile, "created_at": utc_now(),
        "after": str(dependency) if dependency else None, **settings,
    })
    launch_worker(session)
    return session


def run_session(session: Path, resume: bool = False, retry_unavailable: bool = False,
                agent_env_file: Path | None = None) -> int:
    def state(value: str, **extra: Any) -> None:
        write_json_atomic(session / "status.json", {"state": value, "updated_at": utc_now(), **extra})

    with (session / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1
        try:
            spec = read_object(session / "session.json")
            if spec.get("after") and not resume:
                dependency = Path(spec["after"])
                while True:
                    try:
                        report = session_report(dependency)
                    except ValueError:
                        state("queued", detail=f"waiting for readable metadata: {dependency.name}")
                        time.sleep(10)
                        continue
                    if report["state"] == "completed":
                        break
                    state("queued", detail=f"waiting for {dependency.name} ({report['state']})")
                    time.sleep(10)
            environment = dict(os.environ)
            environment.update(spec["environment"])
            if resume:
                benchmark = read_object(session / "matrix/benchmark.json")
                args = ["--resume", str(session / "matrix"), "--concurrency", str(benchmark["concurrency"])]
                if retry_unavailable:
                    args.append("--retry-unavailable")
                saved_env = matrix_args(spec["args"]).agent_env_file
                runtime_env = agent_env_file or saved_env
                if runtime_env is not None:
                    args.extend(["--agent-env-file", str(runtime_env)])
            else:
                args = [*spec["args"], "--output-dir", str(session / "matrix")]
            state("running")
            result = subprocess.run([sys.executable, str(REPO_DIR / "integrations/host/run_host_matrix.py"), *args],
                                    cwd=REPO_DIR, env=environment)
            matrix = session / "matrix"
            report = matrix_report(matrix) if (matrix / "benchmark.json").exists() else {}
            final = report.get("state")
            state(final if final in {"completed", "incomplete"} else "failed", exit_code=result.returncode)
            return result.returncode
        except Exception as error:
            state("failed", detail=str(error))
            print(f"session failed: {error}", file=sys.stderr)
            return 1


def render(report: dict[str, Any]) -> str:
    totals = report["totals"]
    name = Path(report.get("session") or report["matrix"]).name
    lines = [f"{name}: {report['state']}",
             f"{report['completed']}/{report['expected'] if report['expected'] is not None else '?'} completed | "
             f"{totals['passed']} passed | {totals['failed']} failed | {totals['unavailable']} unavailable"]
    if totals["cost_reported_trials"]:
        lines.append(f"Completed-trial known spend: ${totals['known_cost_usd']:.4f} "
                     f"({totals['cost_reported_trials']}/{report['completed']} trials report cost)")
    if report.get("detail"):
        lines.append(report["detail"])
    for category, count in sorted(report.get("failure_categories", {}).items()):
        lines.append(f"  {category}: {count}")
    for active in report.get("active", []):
        lines.append(f"  active: {active['run_id']} (last log update {active['last_log_update_seconds']}s ago)")
    if report.get("invalid_results"):
        lines.append(f"Invalid result artifacts: {len(report['invalid_results'])}")
    if not report.get("managed") and report["state"] == "in_progress":
        lines.append("Unmanaged matrix: controller liveness is not verified.")
    if report.get("log"):
        lines.append(f"Log: {report['log']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=config_path())
    parser.add_argument("--jobs-dir", type=Path, default=REPO_DIR / "jobs")
    commands = parser.add_subparsers(dest="command", required=True,
                                     metavar="{start,status,watch,list,resume}")
    start = commands.add_parser("start", help="start a configured profile in the background")
    start.add_argument("profile")
    start.add_argument("--after", help="wait for a session or matrix to complete")
    status = commands.add_parser("status", help="inspect a session or existing matrix")
    status.add_argument("target", nargs="?", default="latest")
    status.add_argument("--json", action="store_true")
    watch = commands.add_parser("watch", help="watch progress; Ctrl-C only exits the watcher")
    watch.add_argument("target", nargs="?", default="latest")
    watch.add_argument("--interval", type=float, default=5)
    commands.add_parser("list", help="list managed sessions")
    resume = commands.add_parser("resume", help="resume an interrupted managed session")
    resume.add_argument("target")
    resume.add_argument("--retry-unavailable", action="store_true")
    resume.add_argument("--agent-env-file", type=Path)
    worker = commands.add_parser("_run")
    worker.add_argument("session", type=Path)
    worker.add_argument("--resume", action="store_true")
    worker.add_argument("--retry-unavailable", action="store_true")
    worker.add_argument("--agent-env-file", type=Path)
    args = parser.parse_args(argv)
    jobs = args.jobs_dir.expanduser().resolve()
    try:
        if args.command == "_run":
            return run_session(args.session, args.resume, args.retry_unavailable, args.agent_env_file)
        if args.command == "start":
            session = start_session(args.profile, args.config, jobs, args.after)
            print(f"Session: {session}\nWatch: ./replaybook-bench watch {session}")
            return 0
        if args.command == "list":
            for path in sorted((jobs / "sessions").glob("*/session.json")):
                print(render(session_report(path.parent)) + "\n")
            return 0
        target = resolve_target(args.target, jobs)
        if args.command == "resume":
            if not (target / "session.json").exists():
                raise ValueError("resume requires a managed session; use run_host_matrix.py --resume for an unmanaged matrix")
            launch_worker(target, resume=True, retry_unavailable=args.retry_unavailable,
                          agent_env_file=args.agent_env_file)
            print(f"Resuming: {target}")
            return 0
        if args.command == "status":
            report = session_report(target)
            print(json.dumps(report, indent=2) if args.json else render(report))
            return 0
        if not math.isfinite(args.interval) or args.interval <= 0:
            raise ValueError("watch interval must be positive and finite")
        while True:
            report = session_report(target)
            if sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(render(report), flush=True)
            if report["state"] in TERMINAL_STATES:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nWatcher closed; benchmark workers continue.")
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
