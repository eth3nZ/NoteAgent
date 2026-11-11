import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.check_secrets import VERSION, install_hook, scan


class SecretScanTests(unittest.TestCase):
    def test_leak_output_never_forwards_scanner_secret_or_match(self):
        fake = "private-" + "credential-for-scanner-test"
        seen = []
        def run(command, **kwargs):
            seen.append(command)
            if command[-1] == "version":
                return SimpleNamespace(returncode=0, stdout=VERSION)
            report = Path(next(value.split("=", 1)[1] for value in command if value.startswith("--report-path=")))
            report.write_text(json.dumps([{"Secret": fake, "Match": fake, "File": "config.txt",
                                          "StartLine": 2, "RuleID": "generic-api-key"}]))
            return SimpleNamespace(returncode=1, stdout=fake, stderr=fake)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch("scripts.check_secrets.subprocess.run", side_effect=run), contextlib.redirect_stdout(output):
            result = scan(directory, staged=True, binary="fake-scanner")
        self.assertEqual(result, 1)
        self.assertNotIn(fake, output.getvalue())
        self.assertIn("config.txt", output.getvalue())
        self.assertIn("--redact=100", seen[1])
        self.assertIn("--staged", seen[1])

    def test_full_history_scan_and_infrastructure_errors_fail_closed(self):
        for staged in (False, True):
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                return SimpleNamespace(returncode=0, stdout=VERSION) if command[-1] == "version" else SimpleNamespace(returncode=2)
            with patch("scripts.check_secrets.subprocess.run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scan(".", staged=staged, binary="fake-scanner"), 2)
            self.assertIn("--staged" if staged else "--log-opts=--all", commands[1])

    def test_wrong_version_is_not_silently_accepted(self):
        with patch("scripts.check_secrets.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="old")) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scan(".", binary="fake-scanner"), 2)
        self.assertEqual(run.call_count, 1)

    def test_hook_install_never_overwrites_existing_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks" / "pre-commit"
            def run(*args, **kwargs):
                return SimpleNamespace(returncode=0, stdout=str(path))
            with patch("scripts.check_secrets.subprocess.run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install_hook(directory), 0)
                original = path.read_bytes()
                self.assertEqual(install_hook(directory), 2)
            self.assertEqual(path.read_bytes(), original)
            self.assertIn("--staged", path.read_text())
            self.assertTrue(path.stat().st_mode & 0o111)

    def test_gitignore_covers_private_files_but_allows_examples(self):
        paths = [".env", ".env.local", ".env.example", ".env.dev.example", ".noteagent_config.json",
                 "tasks.sqlite3", "tasks.sqlite3-journal", "private.pem", "api.secrets.json"]
        result = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(paths),
                                text=True, capture_output=True, check=False)
        ignored = set(result.stdout.splitlines())
        self.assertEqual(ignored, set(paths) - {".env.example", ".env.dev.example"})
