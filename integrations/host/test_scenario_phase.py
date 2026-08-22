from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from integrations.host.scenario_phase import (
    FAILURE_FILE,
    PhaseFailure,
    describe_manifest,
    image_artifacts_config,
    leak_audit_config,
    load_manifest,
    run_phase,
)


class TestHandler(BaseHTTPRequestHandler):
    pool_size = 1
    checkout_count = 0
    completed: set[str] = set()
    poll_count: dict[str, int] = {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.respond(200, "ok")
        elif self.path == "/pool":
            self.respond(200, str(type(self).pool_size))
        else:
            self.respond(404, "missing")

    def do_POST(self) -> None:  # noqa: N802
        identifier = self.path.rsplit("/", 1)[-1]
        type(self).checkout_count += 1
        if type(self).pool_size == 1 and type(self).checkout_count % 2 == 0:
            self.respond(503, "database pool exhausted")
            return
        type(self).completed.add(identifier)
        self.respond(200, "completed")

    def respond(self, status: int, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ScenarioPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        TestHandler.pool_size = 1
        TestHandler.checkout_count = 0
        TestHandler.completed = set()
        TestHandler.poll_count = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def write_manifest(self, root: Path) -> Path:
        path = root / "scenario.toml"
        path.write_text(
            """
[scenario]
version = 1
nixos_config = "nixos.nix"
instruction = "instruction.md"
oracle = "oracle.sh"
required_services = ["postgresql.service", "checkout-web.service", "nginx.service"]
restart_services = ["checkout-web.service", "nginx.service"]
image_artifacts = ["evidence/topology.png", "evidence/dashboard.webp"]

[guest_leak_audit]
forbidden_strings = ["intentionally undersized pool", "pool exhaustion benchmark"]
scan_paths = ["/etc/replaybook", "/var/lib/checkout"]

[[preflight.steps]]
type = "wait_http"
path = "/health"
expected_body = "ok"
timeout_seconds = 1
interval_seconds = 0.01

[[preflight.steps]]
type = "concurrent_http"
method = "POST"
path = "/checkouts/{id}"
count = 4
concurrency = 4
expected_status = 200
min_success = 1
max_success = 3
record_failed_as = "failed_checkouts"
failure_category = "incident_not_reproduced"

[[verify.steps]]
type = "wait_http"
path = "/pool"
body_integer_min = 4
timeout_seconds = 0.1
interval_seconds = 0.01
failure_category = "database_pool_exhausted"

[[verify.steps]]
type = "replay_http"
method = "POST"
path = "/checkouts/{id}"
ids_from = "failed_checkouts"
expected_status = 200
failure_category = "database_pool_exhausted"

[[verify.steps]]
type = "concurrent_http"
method = "POST"
path = "/checkouts/{id}"
id_prefix = "verify-{phase}"
count = 8
concurrency = 8
expected_status = 200
failure_category = "database_pool_exhausted"
""".strip()
            + "\n"
        )
        return path

    def test_describes_typed_scenario_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.write_manifest(Path(temporary))
            description = describe_manifest(manifest)
            self.assertEqual(description["version"], 1)
            self.assertEqual(description["nixos_config"], "nixos.nix")
            self.assertEqual(description["preflight_steps"], 2)
            self.assertEqual(description["verify_steps"], 3)
            self.assertEqual(
                description["image_artifacts"],
                ["evidence/topology.png", "evidence/dashboard.webp"],
            )
            self.assertEqual(
                description["guest_leak_audit"]["forbidden_strings"],
                ["intentionally undersized pool", "pool exhaustion benchmark"],
            )
            self.assertEqual(
                description["guest_leak_audit"]["scan_paths"],
                ["/etc/replaybook", "/var/lib/checkout"],
            )

    def test_rejects_unsafe_guest_leak_audit_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.write_manifest(Path(temporary))
            text = manifest.read_text().replace(
                'scan_paths = ["/etc/replaybook", "/var/lib/checkout"]',
                'scan_paths = ["/"]',
            )
            manifest.write_text(text)
            with self.assertRaisesRegex(ValueError, "safe absolute guest paths"):
                leak_audit_config(load_manifest(manifest))

    def test_rejects_unsafe_or_unsupported_image_artifacts(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe relative paths"):
            image_artifacts_config({"image_artifacts": ["../answer.png"]})
        with self.assertRaisesRegex(ValueError, "PNG, JPEG, GIF, and WebP"):
            image_artifacts_config({"image_artifacts": ["evidence.txt"]})
        with self.assertRaisesRegex(ValueError, "duplicates"):
            image_artifacts_config({"image_artifacts": ["same.png", "same.png"]})

    def test_records_failed_ids_then_replays_them_after_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.write_manifest(root)
            state_dir = root / "state"
            run_phase(manifest, "preflight", self.base_url, state_dir)
            state = json.loads((state_dir / "scenario-state.json").read_text())
            self.assertGreaterEqual(len(state["failed_checkouts"]), 1)

            TestHandler.pool_size = 4
            run_phase(manifest, "immediate", self.base_url, state_dir)
            self.assertTrue(set(state["failed_checkouts"]).issubset(TestHandler.completed))
            self.assertFalse((state_dir / FAILURE_FILE).exists())

    def test_writes_structured_failure_category(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.write_manifest(root)
            state_dir = root / "state"
            run_phase(manifest, "preflight", self.base_url, state_dir)

            with self.assertRaisesRegex(PhaseFailure, "HTTP assertion failed"):
                run_phase(manifest, "service_restart", self.base_url, state_dir)

            failure = json.loads((state_dir / FAILURE_FILE).read_text())
            self.assertEqual(failure["category"], "database_pool_exhausted")
            self.assertEqual(failure["phase"], "service_restart")
            self.assertEqual(failure["step"], 1)

    def test_replay_http_polls_controller_ids_until_they_match(self) -> None:
        class PollingHandler(TestHandler):
            def do_GET(self) -> None:  # noqa: N802
                identifier = self.path.rsplit("/", 1)[-1]
                count = type(self).poll_count.get(identifier, 0) + 1
                type(self).poll_count[identifier] = count
                self.respond(200, "completed" if count >= 2 else "pending")

        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PollingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "scenario.toml"
            manifest.write_text(
                """
[[verify.steps]]
type = "replay_http"
path = "/jobs/{id}"
ids_from = "backlog"
expected_body = "completed"
timeout_seconds = 1
interval_seconds = 0.01
failure_category = "backlog_not_recovered"
""".strip()
                + "\n"
            )
            state_dir = root / "state"
            state_dir.mkdir()
            (state_dir / "scenario-state.json").write_text('{"backlog":["job-1","job-2"]}\n')

            run_phase(manifest, "immediate", self.base_url, state_dir)

            self.assertGreaterEqual(PollingHandler.poll_count["job-1"], 2)
            self.assertGreaterEqual(PollingHandler.poll_count["job-2"], 2)

    def test_rejects_unknown_step_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "scenario.toml"
            manifest.write_text('[[preflight.steps]]\ntype = "shell_magic"\n')
            with self.assertRaisesRegex(ValueError, "unsupported step type"):
                run_phase(manifest, "preflight", self.base_url, root / "state")


if __name__ == "__main__":
    unittest.main()
