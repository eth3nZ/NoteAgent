"""Development-only Gitleaks check. Print locations, never matches or secret values."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


VERSION = "8.30.1"


def install_hook(repo):
    result = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=repo,
                            capture_output=True, text=True)
    if result.returncode:
        print("Cannot locate this repository's Git hooks.")
        return 2
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(repo) / path
    if path.exists() or path.is_symlink():
        print("Existing pre-commit hook retained. Add: python3 scripts/check_secrets.py --staged")
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write('#!/bin/sh\nexec python3 scripts/check_secrets.py --staged\n')
        path.chmod(0o755)
    except OSError:
        print("Could not install the hook. Existing hooks were retained.")
        return 2
    print("Installed staged-secret check. It requires Gitleaks " + VERSION + ".")
    return 0


def scan(repo, *, staged=False, binary=None):
    binary = binary or shutil.which("gitleaks")
    if not binary:
        print("Install Gitleaks " + VERSION + " before checking secrets; see README.")
        return 2
    try:
        version = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=15)
        if version.returncode or version.stdout.strip().removeprefix("v") != VERSION:
            print("This check requires Gitleaks " + VERSION + ".")
            return 2
        with tempfile.TemporaryDirectory(prefix="noteagent-secret-scan-") as directory:
            report = Path(directory) / "report.json"
            command = [binary, "git", "--redact=100", "--no-banner", "--report-format=json",
                       "--report-path=" + str(report)]
            command += ["--pre-commit", "--staged"] if staged else ["--log-opts=--all"]
            command.append(str(Path(repo).resolve()))
            result = subprocess.run(command, capture_output=True, timeout=120)
            if result.returncode == 0:
                print("Secret scan passed (staged changes)." if staged else "Secret scan passed (all reachable Git history).")
                return 0
            if result.returncode == 1 and report.exists():
                findings = json.loads(report.read_text(encoding="utf-8"))
                if not isinstance(findings, list) or not findings:
                    raise ValueError("Invalid scanner report")
                print(f"Commit blocked: {len(findings)} potential secret(s). Remove the credential before retrying.")
                for item in findings:
                    # Gitleaks is also redacted, but never forward its output or Match/Secret fields.
                    print(f"  {str(item.get('File', 'unknown'))!r}:{int(item.get('StartLine', 0))} ({str(item.get('RuleID', 'unknown'))!r})")
                return 1
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        pass
    print("Secret scan could not complete. No scanner output was printed; check the installation and retry.")
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="Check staged changes before committing")
    mode.add_argument("--install-hook", action="store_true", help="Install only when no pre-commit hook exists")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--binary", help="Path to Gitleaks " + VERSION)
    args = parser.parse_args()
    return install_hook(args.repo) if args.install_hook else scan(args.repo, staged=args.staged, binary=args.binary)


if __name__ == "__main__":
    raise SystemExit(main())
