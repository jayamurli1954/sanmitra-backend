from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_FILE = REPO_ROOT / 'app' / 'modules' / 'mandir_compat' / 'router.py'
AUDIT_FILE = REPO_ROOT / 'docs' / 'MANDIR_COMPAT_ENDPOINT_AUDIT.md'

# Intentionally static lookup responses that are still legitimate UX helpers.
REAL_STATIC_ALLOWLIST = {
    '/api/v1/donations/categories',
    '/api/v1/donations/categories/',
    '/api/v1/panchang/display-settings',
    '/api/v1/panchang/display-settings/',
    '/api/v1/panchang/display-settings/cities',
    '/api/v1/sevas/dropdown-options',
    '/api/v1/sevas/lists/priests',
    '/api/v1/accounts/initialize-default',
}


@dataclass(frozen=True)
class RouteInfo:
    route: str
    func_name: str
    lineno: int
    placeholder_like: bool


def _normalize_route(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith('/api/v1'):
        raw = '/api/v1' + raw if raw.startswith('/') else '/api/v1/' + raw
    return raw.rstrip('/') if raw not in {'/api/v1', '/api/v1/'} else '/api/v1'


def _parse_audit_file(path: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    text = path.read_text(encoding='utf-8-sig')
    for line in text.splitlines():
        match = re.match(r'^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|', line)
        if not match:
            continue
        raw_route = match.group(1).strip()
        if ' ' in raw_route:
            raw_route = raw_route.split(None, 1)[1].strip()
        route = _normalize_route(raw_route)
        status = match.group(2).strip().lower()
        statuses[route] = status
    return statuses


def _is_literal_like(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal_like(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        if not node.keys:
            return True
        return all(
            (key is None or _is_literal_like(key)) and _is_literal_like(value)
            for key, value in zip(node.keys, node.values)
        )
    return False


def _is_placeholder_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id == '_ok'
    if isinstance(node, ast.List):
        return len(node.elts) == 0 or all(_is_placeholder_expr(item) for item in node.elts)
    if isinstance(node, ast.Tuple):
        return len(node.elts) == 0 or all(_is_placeholder_expr(item) for item in node.elts)
    if isinstance(node, ast.Set):
        return len(node.elts) == 0 or all(_is_placeholder_expr(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        if not node.keys:
            return True
        return all(
            (key is None or _is_placeholder_expr(key)) and _is_placeholder_expr(value)
            for key, value in zip(node.keys, node.values)
        )
    return _is_literal_like(node)


def _route_paths_for_function(node: ast.AST) -> list[str]:
    routes: list[str] = []
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return routes

    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if not isinstance(dec.func, ast.Attribute):
            continue
        if dec.func.attr not in {'get', 'post', 'put', 'patch', 'delete'}:
            continue
        if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != 'router':
            continue
        if not dec.args:
            continue
        first = dec.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            routes.append(_normalize_route(f'/api/v1{first.value}'))
    return routes


def _function_is_placeholder_like(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False

    returns: list[ast.Return] = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
    if not returns:
        body_nodes = [stmt for stmt in node.body if not isinstance(stmt, ast.Expr)]
        return all(isinstance(stmt, ast.Pass) for stmt in body_nodes)

    return all(ret.value is not None and _is_placeholder_expr(ret.value) for ret in returns)


def collect_routes() -> list[RouteInfo]:
    source = ROUTER_FILE.read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    routes: list[RouteInfo] = []
    seen: set[tuple[str, str]] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = _route_paths_for_function(node)
        if not paths:
            continue
        placeholder_like = _function_is_placeholder_like(node)
        for path in paths:
            key = (path, node.name)
            if key in seen:
                continue
            seen.add(key)
            routes.append(RouteInfo(route=path, func_name=node.name, lineno=node.lineno, placeholder_like=placeholder_like))

    return sorted(routes, key=lambda item: item.route)


def main() -> int:
    if not ROUTER_FILE.exists():
        print(f'ERROR: missing router file: {ROUTER_FILE}')
        return 2
    if not AUDIT_FILE.exists():
        print(f'ERROR: missing audit file: {AUDIT_FILE}')
        return 2

    audit_status = _parse_audit_file(AUDIT_FILE)
    routes = collect_routes()

    errors: list[str] = []
    placeholders = 0

    for route in routes:
        documented_status = audit_status.get(route.route)
        if route.placeholder_like:
            if route.route in REAL_STATIC_ALLOWLIST:
                continue
            placeholders += 1
            if documented_status is None:
                errors.append(
                    f'Undocumented placeholder route: {route.route} (function {route.func_name} @ line {route.lineno})'
                )
            elif documented_status != 'placeholder':
                errors.append(
                    f'Status mismatch: {route.route} is placeholder-like in source but audit marks {documented_status}'
                )
        else:
            if documented_status == 'placeholder':
                errors.append(
                    f'Status mismatch: {route.route} is documented as placeholder but source now looks real'
                )

    print(f'Analysed {len(routes)} Mandir compat routes.')
    print(f'Placeholder-like routes: {placeholders}')

    if errors:
        print('\nMandir compat audit failed:')
        for item in errors:
            print(f'  - {item}')
        return 1

    print('Mandir compat audit passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
