#!/usr/bin/env python3
"""Fail CI when source files contain encoding corruption patterns."""

from __future__ import annotations

import argparse
from pathlib import Path

BAD_TOKENS = (
    "\u00C3",
    "\u00E2\u20AC",
    "\u00E2\u0153",
    "\u00E2\u009D",
    "\u00F0\u0178",
    "\uFFFD",
)
DEFAULT_PATHS = ("app", "scripts", ".github/workflows")
TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".json",
    ".yml",
    ".yaml",
    ".md",
    ".txt",
    ".ini",
}
IGNORE_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".next",
}


def iter_target_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        base = Path(raw_path)
        if not base.exists():
            continue

        if base.is_file():
            if base.suffix.lower() in TEXT_SUFFIXES:
                files.append(base)
            continue

        for item in base.rglob("*"):
            if not item.is_file():
                continue
            if item.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(part in IGNORE_DIR_NAMES for part in item.parts):
                continue
            files.append(item)

    return sorted(set(files))


def check_file(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append(f"{path}: invalid UTF-8 ({exc})")
        return issues

    for token in BAD_TOKENS:
        if token in text:
            issues.append(f"{path}: contains suspicious token {token!r}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Text integrity checker")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS), help="Paths to scan")
    args = parser.parse_args()

    targets = iter_target_files(args.paths)
    if not targets:
        print("No files matched integrity check paths.")
        return 0

    print(f"Scanning {len(targets)} files for UTF-8 + corruption patterns...")
    problems: list[str] = []
    for file_path in targets:
        problems.extend(check_file(file_path))

    if problems:
        print("\nText integrity check failed:")
        for issue in problems:
            print(f" - {issue}")
        return 1

    print("Text integrity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
