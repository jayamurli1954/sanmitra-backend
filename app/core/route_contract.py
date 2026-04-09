from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from fastapi.routing import APIRoute


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
EXCLUDE_PARTS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    ".next",
    "coverage",
    "out",
    "docs",
    "desktop",
    "deliverables",
    "plugins",
    "android",
    "ios",
    "e2e-tests",
    "e2e",
    "backups",
    "backend",
}

PATH_PARAM_RE = re.compile(r"\{[^}/]+\}")
TEMPLATE_PARAM_RE = re.compile(r"\$\{[^}]+\}")
COLON_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
METHOD_IN_OPTIONS_RE = re.compile(r"\bmethod\s*:\s*['\"`]?(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['\"`]?", re.IGNORECASE)

API_CALL_PATTERNS = [
    re.compile(r"\bapi\.(get|post|put|patch|delete)\s*\(\s*'([^'\n\r]+)'", re.IGNORECASE),
    re.compile(r'\bapi\.(get|post|put|patch|delete)\s*\(\s*\"([^\"\n\r]+)\"', re.IGNORECASE),
    re.compile(r"\bapi\.(get|post|put|patch|delete)\s*\(\s*`([^`\n\r]+)`", re.IGNORECASE),
]

FETCH_CALL_PATTERNS = [
    re.compile(r"\bfetch\s*\(\s*'([^'\n\r]+)'(?P<tail>[^)]*)\)", re.IGNORECASE),
    re.compile(r'\bfetch\s*\(\s*\"([^\"\n\r]+)\"(?P<tail>[^)]*)\)', re.IGNORECASE),
    re.compile(r"\bfetch\s*\(\s*`([^`\n\r]+)`(?P<tail>[^)]*)\)", re.IGNORECASE),
]

AXIOS_OBJECT_PATTERNS = [
    re.compile(r"\bapi(?:\.request)?\s*\(\s*\{(?P<body>.*?)\}\s*\)", re.IGNORECASE | re.DOTALL),
]

OBJECT_URL_RE = [
    re.compile(r"\burl\s*:\s*'([^'\n\r]+)'", re.IGNORECASE),
    re.compile(r'\burl\s*:\s*\"([^\"\n\r]+)\"', re.IGNORECASE),
    re.compile(r"\burl\s*:\s*`([^`\n\r]+)`", re.IGNORECASE),
]
OBJECT_METHOD_RE = re.compile(r"\bmethod\s*:\s*['\"`]?(GET|POST|PUT|PATCH|DELETE)['\"`]?", re.IGNORECASE)


@dataclass(frozen=True)
class FrontendRouteUsage:
    app: str
    method: str
    path: str
    file: str
    line: int


@dataclass(frozen=True)
class BackendRoute:
    method: str
    path: str
    pattern: re.Pattern[str]


def default_frontend_roots(repo_root: Path | None = None) -> dict[str, list[Path]]:
    base = (repo_root or REPO_ROOT) / "external-repos"
    candidates = {
        "LegalMitra": [base / "LegalMitra" / "frontend" / "src"],
        "MandirMitra": [base / "MandirMitra" / "frontend" / "src"],
        "InvestMitra": [base / "InvestMitra" / "frontend" / "src"],
        "MitraBooks": [base / "MitraBooks" / "frontend" / "src"],
        "GharMitra": [base / "GharMitra" / "src", base / "GharMitra" / "web" / "src"],
    }

    return {
        app: [path for path in paths if path.exists()]
        for app, paths in candidates.items()
    }


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if any(part in EXCLUDE_PARTS for part in path.parts):
            continue
        yield path


def normalize_frontend_path(raw: str) -> str:
    value = str(raw or "").strip().strip("\"'`")
    if not value:
        return ""

    if value.startswith(("http://", "https://")):
        value = urlparse(value).path or ""

    value = value.split("?", 1)[0].split("#", 1)[0]
    value = value.replace("\\", "/")
    value = TEMPLATE_PARAM_RE.sub("{param}", value)
    value = COLON_PARAM_RE.sub("{param}", value)
    value = re.sub(r"(?<!/)\{param\}$", "", value)
    value = re.sub(r"/{2,}", "/", value)

    if not value.startswith("/"):
        return ""

    if len(value) > 1 and value.endswith("/"):
        value = value[:-1]

    return value


def path_candidates(path: str) -> set[str]:
    normalized = normalize_frontend_path(path)
    if not normalized:
        return set()

    candidates = {normalized}
    if normalized.startswith("/") and not normalized.startswith("/api"):
        candidates.add(normalize_frontend_path(f"/api/v1{normalized}"))
    if normalized.endswith("/"):
        candidates.add(normalized.rstrip("/"))
    return {item for item in candidates if item}


def _path_template_to_pattern(path_template: str) -> re.Pattern[str]:
    normalized = normalize_frontend_path(path_template)
    if not normalized:
        normalized = "/"

    parts: list[str] = []
    cursor = 0
    for match in PATH_PARAM_RE.finditer(normalized):
        parts.append(re.escape(normalized[cursor:match.start()]))
        parts.append(r"[^/]+")
        cursor = match.end()
    parts.append(re.escape(normalized[cursor:]))

    body = "".join(parts)
    if body != "/":
        body = body.rstrip("/")
    return re.compile(rf"^{body}/?$", re.IGNORECASE)


def _line_number(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _extract_api_calls(text: str, app: str, file_path: Path, repo_root: Path) -> list[FrontendRouteUsage]:
    usages: list[FrontendRouteUsage] = []
    rel_file = str(file_path.relative_to(repo_root)).replace("\\", "/")

    for pattern in API_CALL_PATTERNS:
        for match in pattern.finditer(text):
            method = match.group(1).upper()
            raw_path = match.group(2)
            normalized_path = normalize_frontend_path(raw_path)
            if not normalized_path:
                continue
            usages.append(
                FrontendRouteUsage(
                    app=app,
                    method=method,
                    path=normalized_path,
                    file=rel_file,
                    line=_line_number(text, match.start()),
                )
            )

    for pattern in FETCH_CALL_PATTERNS:
        for match in pattern.finditer(text):
            raw_path = match.group(1)
            normalized_path = normalize_frontend_path(raw_path)
            if not normalized_path:
                continue
            tail = match.group("tail") or ""
            method_match = METHOD_IN_OPTIONS_RE.search(tail)
            method = method_match.group(1).upper() if method_match else "GET"
            usages.append(
                FrontendRouteUsage(
                    app=app,
                    method=method,
                    path=normalized_path,
                    file=rel_file,
                    line=_line_number(text, match.start()),
                )
            )

    for pattern in AXIOS_OBJECT_PATTERNS:
        for match in pattern.finditer(text):
            body = match.group("body") or ""
            url_match = None
            for url_pattern in OBJECT_URL_RE:
                url_match = url_pattern.search(body)
                if url_match:
                    break
            if not url_match:
                continue
            method_match = OBJECT_METHOD_RE.search(body)
            method = method_match.group(1).upper() if method_match else "GET"
            normalized_path = normalize_frontend_path(url_match.group(1))
            if not normalized_path:
                continue
            usages.append(
                FrontendRouteUsage(
                    app=app,
                    method=method,
                    path=normalized_path,
                    file=rel_file,
                    line=_line_number(text, match.start()),
                )
            )

    return usages


def collect_frontend_route_usages(repo_root: Path | None = None, roots: dict[str, list[Path]] | None = None) -> list[FrontendRouteUsage]:
    root = repo_root or REPO_ROOT
    frontend_roots = roots or default_frontend_roots(root)
    usages: list[FrontendRouteUsage] = []

    for app_name, app_roots in frontend_roots.items():
        for app_root in app_roots:
            for file_path in iter_source_files(app_root):
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                usages.extend(_extract_api_calls(text, app_name, file_path, root))

    dedup: dict[tuple[str, str, str, str, int], FrontendRouteUsage] = {}
    for usage in usages:
        key = (usage.app, usage.method, usage.path, usage.file, usage.line)
        dedup[key] = usage

    return sorted(
        dedup.values(),
        key=lambda item: (item.app, item.method, item.path, item.file, item.line),
    )


def build_frontend_manifest(usages: list[FrontendRouteUsage]) -> dict:
    grouped: dict[tuple[str, str, str], list[dict[str, int | str]]] = {}
    for usage in usages:
        key = (usage.app, usage.method, usage.path)
        grouped.setdefault(key, []).append({"file": usage.file, "line": usage.line})

    routes = []
    for (app_name, method, path), sources in sorted(grouped.items(), key=lambda item: item[0]):
        sources_sorted = sorted(sources, key=lambda src: (str(src["file"]), int(src["line"])))
        routes.append(
            {
                "app": app_name,
                "method": method,
                "path": path,
                "sources": sources_sorted,
            }
        )

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "route_count": len(routes),
        "routes": routes,
    }


def write_manifest(manifest_path: Path, manifest: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_manifest_routes(manifest_path: Path) -> list[FrontendRouteUsage]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = payload.get("routes", [])

    usages: list[FrontendRouteUsage] = []
    for row in entries:
        app = str(row.get("app") or "unknown")
        method = str(row.get("method") or "GET").upper()
        path = normalize_frontend_path(str(row.get("path") or ""))
        sources = row.get("sources") or []
        if not path:
            continue
        if not isinstance(sources, list) or not sources:
            usages.append(FrontendRouteUsage(app=app, method=method, path=path, file="manifest", line=1))
            continue
        for source in sources:
            file_value = str(source.get("file") or "manifest")
            line_value = int(source.get("line") or 1)
            usages.append(FrontendRouteUsage(app=app, method=method, path=path, file=file_value, line=line_value))

    dedup: dict[tuple[str, str, str, str, int], FrontendRouteUsage] = {}
    for usage in usages:
        key = (usage.app, usage.method, usage.path, usage.file, usage.line)
        dedup[key] = usage

    return sorted(dedup.values(), key=lambda item: (item.app, item.method, item.path, item.file, item.line))


def load_allowlist(allowlist_path: Path | None) -> set[tuple[str, str, str]]:
    if allowlist_path is None or not allowlist_path.exists():
        return set()

    payload = json.loads(allowlist_path.read_text(encoding="utf-8-sig"))
    rows = payload.get("entries", []) if isinstance(payload, dict) else payload
    allowed: set[tuple[str, str, str]] = set()

    if not isinstance(rows, list):
        return allowed

    for row in rows:
        if not isinstance(row, dict):
            continue
        app = str(row.get("app") or "").strip()
        method = str(row.get("method") or "").upper().strip()
        path = normalize_frontend_path(str(row.get("path") or ""))
        if app and method and path:
            allowed.add((app, method, path))

    return allowed


def extract_backend_routes() -> list[BackendRoute]:
    from app.main import app

    backend_routes: list[BackendRoute] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = normalize_frontend_path(route.path)
        if not path:
            continue

        methods = set(route.methods or []) - {"HEAD", "OPTIONS"}
        for method in sorted(methods):
            backend_routes.append(
                BackendRoute(
                    method=method.upper(),
                    path=path,
                    pattern=_path_template_to_pattern(path),
                )
            )

    dedup: dict[tuple[str, str], BackendRoute] = {}
    for route in backend_routes:
        dedup[(route.method, route.path)] = route

    return sorted(dedup.values(), key=lambda item: (item.method, item.path))


def _matches_backend_route(usage: FrontendRouteUsage, backend_routes: list[BackendRoute]) -> bool:
    candidates = path_candidates(usage.path)
    if not candidates:
        return False

    for candidate in candidates:
        for backend_route in backend_routes:
            if backend_route.method != usage.method:
                continue
            if backend_route.pattern.fullmatch(candidate):
                return True
    return False


def find_unmatched_frontend_routes(
    usages: list[FrontendRouteUsage],
    backend_routes: list[BackendRoute],
    *,
    allowlist: set[tuple[str, str, str]] | None = None,
) -> list[FrontendRouteUsage]:
    allowed = allowlist or set()

    unresolved: list[FrontendRouteUsage] = []
    seen_missing: set[tuple[str, str, str]] = set()

    for usage in usages:
        usage_key = (usage.app, usage.method, usage.path)
        if usage_key in allowed:
            continue
        if usage.path in {"/api", "/api/v1"}:
            continue
        if _matches_backend_route(usage, backend_routes):
            continue
        if usage_key in seen_missing:
            continue
        seen_missing.add(usage_key)
        unresolved.append(usage)

    return sorted(unresolved, key=lambda item: (item.app, item.method, item.path, item.file, item.line))


def summarize_by_app(usages: list[FrontendRouteUsage], unresolved: list[FrontendRouteUsage]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}

    unique_total: dict[str, set[tuple[str, str]]] = {}
    for usage in usages:
        unique_total.setdefault(usage.app, set()).add((usage.method, usage.path))

    unique_unresolved: dict[str, set[tuple[str, str]]] = {}
    for usage in unresolved:
        unique_unresolved.setdefault(usage.app, set()).add((usage.method, usage.path))

    for app_name in sorted(unique_total):
        total = len(unique_total.get(app_name, set()))
        missing = len(unique_unresolved.get(app_name, set()))
        summary[app_name] = {
            "total": total,
            "missing": missing,
            "matched": total - missing,
        }

    return summary


