from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.route_contract import (
    build_frontend_manifest,
    collect_frontend_route_usages,
    extract_backend_routes,
    find_unmatched_frontend_routes,
    load_allowlist,
    load_manifest_routes,
    summarize_by_app,
    write_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that frontend API calls map to existing backend routes "
            "(method + path template)."
        )
    )
    parser.add_argument(
        "--manifest",
        default=str(REPO_ROOT / "docs" / "frontend_route_manifest.json"),
        help="Path to route manifest file used by CI when external-repos are unavailable.",
    )
    parser.add_argument(
        "--allowlist",
        default=str(REPO_ROOT / "scripts" / "frontend_backend_route_allowlist.json"),
        help="JSON file with known temporary exceptions.",
    )
    parser.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="Scan local external-repos and refresh the manifest on disk.",
    )
    parser.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Exit with non-zero status if unresolved frontend routes remain.",
    )
    return parser.parse_args()


def _collect_usages(manifest_path: Path, refresh_manifest: bool) -> tuple[list, str]:
    scanned = collect_frontend_route_usages(REPO_ROOT)
    if scanned:
        if refresh_manifest:
            manifest = build_frontend_manifest(scanned)
            write_manifest(manifest_path, manifest)
        return scanned, "live-scan"

    if manifest_path.exists():
        return load_manifest_routes(manifest_path), "manifest"

    return [], "none"


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    allowlist_path = Path(args.allowlist) if args.allowlist else None

    usages, source_mode = _collect_usages(manifest_path, args.refresh_manifest)
    if not usages:
        print("No frontend route usages found (external-repos missing and manifest unavailable).")
        return 0

    if args.refresh_manifest and source_mode == "live-scan":
        print(f"Manifest refreshed: {manifest_path}")

    backend_routes = extract_backend_routes()
    allowlist = load_allowlist(allowlist_path)
    unresolved = find_unmatched_frontend_routes(usages, backend_routes, allowlist=allowlist)
    summary = summarize_by_app(usages, unresolved)

    print(f"Route source: {source_mode}")
    print(f"Backend route definitions: {len(backend_routes)}")
    print(f"Frontend route usages (dedup by source): {len(usages)}")
    print("")

    for app_name, stats in summary.items():
        print(
            f"{app_name}: total={stats['total']} matched={stats['matched']} missing={stats['missing']}"
        )

    if unresolved:
        print("\nUnresolved frontend routes:")
        for route in unresolved[:200]:
            print(
                f"- [{route.app}] {route.method} {route.path} "
                f"(first seen at {route.file}:{route.line})"
            )
        if len(unresolved) > 200:
            remaining = len(unresolved) - 200
            print(f"... and {remaining} more")

    if args.fail_on_missing and unresolved:
        print("\nRoute contract check failed: unresolved frontend routes found.")
        return 1

    print("\nRoute contract check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
