from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from integrations.host.guest_leak_audit import scan_archive, scan_file


class GuestLeakAuditTests(unittest.TestCase):
    def test_detects_case_insensitive_surface_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            surface = Path(temporary) / "systemd.txt"
            surface.write_text("Description=Partial Rollout Worker\n")
            self.assertEqual(scan_file(surface, [b"partial rollout"]), 1)

    def test_detects_archive_member_without_extracting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "guest.tar.gz"
            payload = b"# intentionally wrong database for benchmark\n"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("etc/replaybook/worker.env")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            self.assertEqual(
                scan_archive(archive_path, [b"wrong database"]),
                (1, "etc/replaybook/worker.env"),
            )

    def test_accepts_realistic_operational_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            surface = Path(temporary) / "journal.txt"
            surface.write_text("connect() failed to 127.0.0.1:3001\n")
            self.assertIsNone(scan_file(surface, [b"wrong upstream port"]))

    def test_cli_redacts_the_forbidden_string(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "scenario.toml"
            manifest.write_text(
                '[guest_leak_audit]\nforbidden_strings = ["secret answer label"]\n'
            )
            surface = root / "paths.txt"
            surface.write_text("Description=secret answer label\n")
            result = subprocess.run(
                [
                    "python",
                    str(Path(__file__).with_name("guest_leak_audit.py")),
                    str(manifest),
                    "--surface",
                    f"paths={surface}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("forbidden string #1 in paths", result.stdout)
            self.assertNotIn("secret answer label", result.stdout)


if __name__ == "__main__":
    unittest.main()
