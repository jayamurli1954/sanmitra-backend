#!/usr/bin/env python3
"""Release gate for SanMitra backend deployments."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TAG_RE = re.compile(r"^backend-v\d+\.\d+\.\d+$")

FOCUSED_TESTS = [
    "tests/test_app_tenant_resolvers.py",
    "tests/test_auth_tenant_policy.py",
    "tests/test_accounting_app_key_isolation.py",
    "tests/test_accounting_context_isolation.py",
    "tests/test_mandir_reports.py",
    "tests/test_mandir_posting_guardrails.py",
    "tests/test_accounting_validation.py",
]


def run(command: list[str]) -> None:
    print(f"+ {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def capture(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip())
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def read_version() -> str:
    if not VERSION_FILE.exists():
        print("VERSION file is missing.")
        raise SystemExit(1)
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        print(f"VERSION must use MAJOR.MINOR.PATCH format, found: {version!r}")
        raise SystemExit(1)
    return version


def require_clean_worktree() -> None:
    status = capture(["git", "status", "--short"])
    if status:
        print("Release preflight requires a clean git worktree.")
        print(status)
        raise SystemExit(1)


def require_tag(expected_tag: str, *, require_current_tag: bool) -> None:
    if not TAG_RE.fullmatch(expected_tag):
        print(f"Release tag must match backend-vMAJOR.MINOR.PATCH, found: {expected_tag!r}")
        raise SystemExit(1)

    tags = capture(["git", "tag", "--list", expected_tag])
    if expected_tag not in tags.splitlines():
        print(f"Missing required release tag: {expected_tag}")
        print(f"Create it after validation: git tag {expected_tag}")
        raise SystemExit(1)

    if require_current_tag:
        tag_commit = capture(["git", "rev-list", "-n", "1", expected_tag])
        head_commit = capture(["git", "rev-parse", "HEAD"])
        if tag_commit != head_commit:
            print(f"{expected_tag} does not point at current HEAD.")
            print(f"tag:  {tag_commit}")
            print(f"HEAD: {head_commit}")
            raise SystemExit(1)


def existing_tests(paths: list[str]) -> list[str]:
    return [path for path in paths if (ROOT / path).exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run release checks before staging/production deploy.")
    parser.add_argument("--target", choices=["staging", "production"], default="staging")
    parser.add_argument("--expected-tag", help="Expected backend release tag, e.g. backend-v1.2.0")
    parser.add_argument("--require-current-tag", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    version = read_version()
    expected_tag = args.expected_tag or f"backend-v{version}"

    if not args.allow_dirty:
        require_clean_worktree()

    if args.target == "production" or args.require_current_tag:
        require_tag(expected_tag, require_current_tag=args.require_current_tag)

    run([sys.executable, "-m", "compileall", "app", "scripts", "tests"])
    run([sys.executable, "scripts/check_text_integrity.py", "app", "scripts", ".github/workflows"])
    run([sys.executable, "scripts/check_repository_safety.py"])

    if not args.skip_tests:
        tests = existing_tests(FOCUSED_TESTS)
        if tests:
            run([sys.executable, "-m", "pytest", *tests, "-q"])

    print(f"Release preflight passed for {args.target}: {expected_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
