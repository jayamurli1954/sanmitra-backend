#!/usr/bin/env python3
"""Fail when repository safety boundaries are violated."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

DENY_PREFIXES = (
    "external-repos/",
)
DENY_PATTERNS = (
    re.compile(r"(^|/)\.env($|\.)", re.IGNORECASE),
    re.compile(r"(^|/).+\.pem$", re.IGNORECASE),
    re.compile(r"(^|/).+\.key$", re.IGNORECASE),
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("Unable to run git ls-files.")
        print(result.stderr.strip())
        raise SystemExit(2)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = tracked_files()

    violations: list[str] = []

    for rel in files:
        normalized = rel.replace("\\", "/")
        if normalized.lower().endswith(".env.example"):
            continue
        if any(normalized.startswith(prefix) for prefix in DENY_PREFIXES):
            violations.append(f"Tracked file under forbidden prefix: {normalized}")
            continue

        for pattern in DENY_PATTERNS:
            if pattern.search(normalized):
                violations.append(f"Potential secret file tracked: {normalized}")
                break

    if violations:
        print("Repository safety check failed:")
        for item in violations:
            print(f" - {item}")
        print("\nFix by untracking files and updating .gitignore where needed.")
        return 1

    print(f"Repository safety check passed for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
