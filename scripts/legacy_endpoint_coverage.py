import ast
import json
import re
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "logs" / "load-test" / "legacy_endpoint_coverage.json"

SOURCE_EXTS = {".js", ".ts", ".jsx", ".tsx", ".html"}
EXCLUDE_PARTS = {
    "node_modules",
    "dist",
    "build",
    ".git",
    ".next",
    "coverage",
    "out",
    "docs",
    "e2e-tests",
    "__tests__",
    "desktop",
    "deliverables",
    "plugins",
}

LEGACY_APPS = [
    {
        "name": "LegalMitra",
        "roots": [REPO_ROOT / "external-repos" / "LegalMitra" / "frontend"],
        "patterns": [
            re.compile(r"/api/v1/[A-Za-z0-9_\-\{\}/]+"),
            re.compile(r"/api/v1\b"),
        ],
    },
    {
        "name": "MandirMitra",
        "roots": [REPO_ROOT / "external-repos" / "MandirMitra" / "frontend"],
        "patterns": [
            re.compile(r"/api/v1/[A-Za-z0-9_\-\{\}/]+"),
            re.compile(r"/api/v1\b"),
        ],
    },
    {
        "name": "GruhaMitra",
        "roots": [REPO_ROOT / "external-repos" / "GharMitra"],
        "patterns": [
            re.compile(r"/api/[A-Za-z0-9_\-\{\}/]+"),
            re.compile(r"/api\b"),
        ],
    },
    {
        "name": "MitraBooks",
        "roots": [REPO_ROOT / "external-repos" / "MitraBooks" / "frontend"],
        "patterns": [
            re.compile(r"/api/v1/[A-Za-z0-9_\-\{\}/]+"),
            re.compile(r"/api/v1\b"),
        ],
    },
    {
        "name": "InvestMitra",
        "roots": [REPO_ROOT / "external-repos" / "InvestMitra" / "frontend"],
        "patterns": [
            re.compile(r"/api/[A-Za-z0-9_\-\{\}/]+"),
            re.compile(r"/api\b"),
        ],
    },
]

HTTP_PATH_RE = re.compile(r"https?://[^\s\"'`]+")
TEMPLATE_SEG_RE = re.compile(r"/\$\{[^}]+\}")
TRAILING_PARAM_RE = re.compile(r"/\{[^}/]+\}")
INVALID_PATHS = {"/api/n", "/api/v1/n"}


def _normalize(raw: str) -> str:
    value = raw.strip().strip("\"'`")
    if not value:
        return ""

    if value.startswith(("http://", "https://")):
        value = urlparse(value).path or ""

    value = value.replace("\\", "/")
    value = value.split("?", 1)[0]
    value = TEMPLATE_SEG_RE.sub("", value)

    if value.endswith("/") and value not in {"/", "/api", "/api/v1"}:
        value = value[:-1]

    return value


def _canonical(route: str) -> str:
    route = _normalize(route)
    route = TRAILING_PARAM_RE.sub("", route)
    if route.endswith("/") and route not in {"/", "/api", "/api/v1"}:
        route = route[:-1]
    return route


def _iter_source_files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTS:
            continue
        if any(part in EXCLUDE_PARTS for part in path.parts):
            continue
        yield path


def _collect_candidate(path: str, found: set[str]) -> None:
    if not path.startswith("/api"):
        return
    if path in INVALID_PATHS:
        return
    found.add(path)


def collect_legacy_paths(app: dict) -> list[str]:
    found: set[str] = set()
    for root in app["roots"]:
        for file_path in _iter_source_files(root):
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for raw in HTTP_PATH_RE.findall(text):
                _collect_candidate(_normalize(raw), found)

            for pattern in app["patterns"]:
                for raw in pattern.findall(text):
                    _collect_candidate(_normalize(raw), found)

    return sorted(found)


def _add_backend_route(route: str, backend_exact: set[str], backend_canonical: set[str]) -> None:
    route = _normalize(route)
    if not route:
        return

    candidates = {route}
    if route.startswith("/") and not route.startswith("/api"):
        candidates.add(_normalize(f"/api/v1{route}"))

    for item in candidates:
        backend_exact.add(item)
        backend_canonical.add(_canonical(item))


def extract_backend_routes() -> tuple[set[str], set[str]]:
    backend_exact: set[str] = set()
    backend_canonical: set[str] = set()

    for file_path in (REPO_ROOT / "app").rglob("*.py"):
        try:
            source = file_path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source)
        except Exception:
            continue

        router_prefix = ""
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not (isinstance(node.value.func, ast.Name) and node.value.func.id == "APIRouter"):
                continue
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    router_prefix = kw.value.value

        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "router":
                    continue
                if not dec.args:
                    continue
                arg0 = dec.args[0]
                if not isinstance(arg0, ast.Constant) or not isinstance(arg0.value, str):
                    continue

                route = _normalize(f"{router_prefix}{arg0.value}")
                _add_backend_route(route, backend_exact, backend_canonical)

    return backend_exact, backend_canonical


def is_matched(path: str, backend_exact: set[str], backend_canonical: set[str]) -> bool:
    if path in {"/api", "/api/v1"}:
        return True

    if path in backend_exact:
        return True

    cp = _canonical(path)
    if cp in backend_canonical:
        return True

    for route in backend_exact:
        if route.startswith(path + "/"):
            return True

    for route in backend_canonical:
        if route.startswith(cp + "/"):
            return True

    return False


def main() -> None:
    backend_exact, backend_canonical = extract_backend_routes()

    results = []
    for app in LEGACY_APPS:
        paths = collect_legacy_paths(app)
        matched = [p for p in paths if is_matched(p, backend_exact, backend_canonical)]
        missing = [p for p in paths if p not in matched]

        results.append(
            {
                "app": app["name"],
                "total": len(paths),
                "matched_count": len(matched),
                "missing_count": len(missing),
                "matched": matched,
                "missing": missing,
            }
        )

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    for row in results:
        print(f"{row['app']}: total={row['total']} matched={row['matched_count']} missing={row['missing_count']}")


if __name__ == "__main__":
    main()
