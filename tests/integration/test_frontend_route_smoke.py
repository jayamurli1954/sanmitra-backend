from __future__ import annotations

from pathlib import Path

from app.core.route_contract import (
    extract_backend_routes,
    find_unmatched_frontend_routes,
    load_allowlist,
    load_manifest_routes,
)


def test_frontend_route_manifest_maps_to_backend_routes() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = repo_root / "docs" / "frontend_route_manifest.json"
    allowlist_path = repo_root / "scripts" / "frontend_backend_route_allowlist.json"

    assert manifest_path.exists(), (
        "Route manifest is missing. Run: "
        "python scripts/check_frontend_backend_route_contract.py --refresh-manifest"
    )

    usages = load_manifest_routes(manifest_path)
    assert usages, "Route manifest is empty. Refresh it from local external-repos before committing."

    backend_routes = extract_backend_routes()
    allowlist = load_allowlist(allowlist_path)
    unresolved = find_unmatched_frontend_routes(usages, backend_routes, allowlist=allowlist)

    if unresolved:
        lines = [
            f"{item.app} {item.method} {item.path} (e.g. {item.file}:{item.line})"
            for item in unresolved[:25]
        ]
        details = "\n".join(lines)
        raise AssertionError(
            "Frontend/backend route contract has unresolved routes:\n"
            f"{details}\n"
            "Update backend routes, or explicitly allowlist temporary exceptions in "
            "scripts/frontend_backend_route_allowlist.json."
        )
