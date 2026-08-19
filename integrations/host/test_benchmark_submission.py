from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.host.benchmark_submission import (
    SubmissionError,
    accept_bundle,
    create_bundle,
    submission_id,
    submit_bundle,
    validate_bundle,
    validate_directory,
    validate_path,
)
from integrations.host.test_publish_benchmarks import summary


class BenchmarkSubmissionTests(unittest.TestCase):
    def write_summary(self, root: Path, value: dict) -> Path:
        path = root / "matrix" / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
        return path

    def test_bundle_is_content_addressed_and_strips_private_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, summary("model/a"))
            bundle = create_bundle([path], submitter="example")

        self.assertEqual(bundle["submission_id"], submission_id(bundle["evidence"]))
        self.assertEqual(bundle["submitter"], "example")
        encoded = json.dumps(bundle)
        self.assertNotIn("result_file", encoded)
        self.assertNotIn("transcript_file", encoded)
        self.assertNotIn("/tmp/private", encoded)
        self.assertEqual(bundle["evidence"]["sources"][0]["source"], "matrix-001")

    def test_bundle_redacts_absolute_paths_in_failure_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("model/a", reward=0)
            value["runs"][0]["failure"] = "read /home/alice/secrets/token.env"
            bundle = create_bundle([self.write_summary(root, value)])

        self.assertEqual(
            bundle["evidence"]["sources"][0]["runs"][0]["failure"],
            "read <redacted-path>",
        )

    def test_validation_rejects_tampered_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = create_bundle([self.write_summary(root, summary("model/a"))])
            bundle["evidence"]["sources"][0]["runs"][0]["reward"] = 0

            with self.assertRaisesRegex(SubmissionError, "submission_id"):
                validate_bundle(bundle, Path("bundle.json"))

    def test_validation_rejects_rehashed_invalid_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = create_bundle([self.write_summary(root, summary("model/a"))])
            bundle["evidence"]["totals"]["passed"] = 99
            bundle["submission_id"] = submission_id(bundle["evidence"])

            with self.assertRaisesRegex(SubmissionError, "aggregate totals"):
                validate_bundle(bundle, Path("bundle.json"))

    def test_validation_rejects_absolute_paths_even_with_valid_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = create_bundle([self.write_summary(root, summary("model/a"))])
            bundle["evidence"]["sources"][0]["runs"][0]["failure"] = (
                "/home/alice/private.log"
            )
            bundle["submission_id"] = submission_id(bundle["evidence"])

            with self.assertRaisesRegex(SubmissionError, "absolute local path"):
                validate_bundle(bundle, Path("bundle.json"))

    def test_validate_path_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary_path = self.write_summary(root, summary("model/a"))
            bundle = create_bundle([summary_path])
            bundle_path = root / "submissions" / f"{bundle['submission_id']}.json"
            bundle_path.parent.mkdir()
            bundle_path.write_text(json.dumps(bundle))

            self.assertEqual(validate_path(bundle_path)["submission_id"], bundle["submission_id"])
            self.assertEqual(validate_directory(bundle_path.parent), 1)
            self.assertEqual(validate_directory(root / "empty"), 0)

    def test_directory_requires_content_addressed_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary_path = self.write_summary(root, summary("model/a"))
            bundle = create_bundle([summary_path])
            submissions = root / "submissions"
            submissions.mkdir()
            (submissions / "friendly-name.json").write_text(json.dumps(bundle))

            with self.assertRaisesRegex(SubmissionError, "filename"):
                validate_directory(submissions)

    def test_bundle_rejects_an_incomplete_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("model/a")
            value["benchmark"]["attempts"] = 2
            path = self.write_summary(root, value)

            with self.assertRaisesRegex(Exception, "missing"):
                create_bundle([path])

    def test_accept_promotes_the_exact_reviewed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary_path = self.write_summary(root, summary("model/a"))
            bundle = create_bundle([summary_path], submitter="example")
            bundle_path = root / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))

            with patch(
                "integrations.host.benchmark_submission.store_release"
            ) as store:
                release = accept_bundle(
                    bundle_path, version="20260819.0.0", root=root
                )

        store.assert_called_once_with(root, release)
        self.assertEqual(release["submission_id"], bundle["submission_id"])
        self.assertEqual(release["submitted_by"], "example")
        self.assertEqual(release["totals"]["passed"], 1)

    def test_submit_uses_contributor_fork_and_opens_pull_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary_path = self.write_summary(root, summary("model/a"))
            bundle = create_bundle([summary_path])
            bundle_path = root.parent / f"bundle-{bundle['submission_id']}.json"
            bundle_path.write_text(json.dumps(bundle))
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                output = ""
                if command[:3] == ["git", "branch", "--show-current"]:
                    output = "main\n"
                elif command[:3] == ["gh", "api", "user"]:
                    output = "contributor\n"
                elif command == ["git", "remote"]:
                    output = "origin\n"
                elif command[:3] == ["gh", "pr", "create"]:
                    output = "https://github.com/ducks/replaybook/pull/123\n"
                return subprocess.CompletedProcess(command, 0, output, "")

            try:
                url = submit_bundle(
                    bundle_path,
                    root=root,
                    target_repo="ducks/replaybook",
                    base_branch="main",
                    run=fake_run,
                )
            finally:
                bundle_path.unlink(missing_ok=True)

        self.assertEqual(url, "https://github.com/ducks/replaybook/pull/123")
        self.assertTrue(any(command[:3] == ["gh", "repo", "fork"] for command in commands))
        self.assertTrue(any(command[:3] == ["git", "push", "-u"] for command in commands))
        self.assertTrue(any(command[:3] == ["gh", "pr", "create"] for command in commands))


if __name__ == "__main__":
    unittest.main()
