from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_BACKEND_DIR = REPO_ROOT / "external-repos" / "GharMitra" / "backend"
OUTPUT_JSON = REPO_ROOT / "docs" / "gruhamitra_api_gap_report.json"


@dataclass(frozen=True)
class Route:
    method: str
    path: str


def _extract_routes(cwd: Path) -> set[Route]:
    script = r"""
import json
from fastapi.routing import APIRoute
from app.main import app

rows = []
for route in app.routes:
    if isinstance(route, APIRoute):
        methods = sorted(set(route.methods or []) - {"HEAD", "OPTIONS"})
        for method in methods:
            rows.append({"method": method, "path": route.path})

print(json.dumps(rows))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    routes: set[Route] = set()
    for row in payload:
        method = str(row.get("method") or "").strip().upper()
        path = str(row.get("path") or "").strip()
        if not method or not path:
            continue
        normalized = path.rstrip("/") or "/"
        routes.add(Route(method=method, path=normalized))
    return routes


def _candidates(path: str) -> set[str]:
    normalized = path.rstrip("/") or "/"
    out = {normalized}
    if normalized.startswith("/api/"):
        out.add(("/api/v1/" + normalized[len("/api/"):]).rstrip("/") or "/")
    elif normalized == "/api":
        out.add("/api/v1")
    return out


def _missing_routes(legacy: set[Route], unified: set[Route]) -> list[Route]:
    unified_pairs = {(r.method, r.path) for r in unified}
    missing: list[Route] = []
    for route in sorted(legacy, key=lambda r: (r.method, r.path)):
        if any((route.method, cand) in unified_pairs for cand in _candidates(route.path)):
            continue
        missing.append(route)
    return missing


def main() -> int:
    if not LEGACY_BACKEND_DIR.exists():
        print(f"Legacy backend path not found: {LEGACY_BACKEND_DIR}")
        return 1

    legacy_routes = _extract_routes(LEGACY_BACKEND_DIR)
    unified_routes = _extract_routes(REPO_ROOT)

    missing = _missing_routes(legacy_routes, unified_routes)
    report = {
        "legacy_route_count": len(legacy_routes),
        "unified_route_count": len(unified_routes),
        "missing_in_unified_count": len(missing),
        "missing_in_unified": [
            {"method": route.method, "path": route.path}
            for route in missing
        ],
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Legacy routes: {len(legacy_routes)}")
    print(f"Unified routes: {len(unified_routes)}")
    print(f"Missing in unified: {len(missing)}")
    print(f"Report written: {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
