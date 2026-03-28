#!/usr/bin/env python
"""Unified backend load test harness for SanMitra multi-frontend traffic.

Scenario focus:
- Concurrent login burst across all 5 app keys.
- Representative post-login API operations per app.
- JSON + Markdown reports with latency/error breakdowns.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


APP_KEYS = [
    "legalmitra",
    "gruhamitra",
    "mandirmitra",
    "mitrabooks",
    "investmitra",
]


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    body: dict[str, Any] | None = None
    weight: int = 1


SCENARIOS: dict[str, list[RequestSpec]] = {
    "legalmitra": [
        RequestSpec("GET", "/api/v1/major-cases", weight=3),
        RequestSpec("GET", "/api/v1/legal-news", weight=3),
        RequestSpec("GET", "/api/v1/v2/templates", weight=2),
        RequestSpec(
            "POST",
            "/api/v1/legal-research",
            body={
                "query": "Summarize latest major Supreme Court and High Court legal developments.",
                "query_type": "research",
            },
            weight=1,
        ),
    ],
    "gruhamitra": [
        RequestSpec("GET", "/api/v1/users/me", weight=3),
        RequestSpec("GET", "/api/v1/onboarding-requests", weight=2),
        RequestSpec("GET", "/api/v1/tenants", weight=1),
    ],
    "mandirmitra": [
        RequestSpec("GET", "/api/v1/users/me", weight=3),
        RequestSpec("GET", "/api/v1/tenants", weight=2),
        RequestSpec("GET", "/api/v1/onboarding-requests", weight=2),
    ],
    "mitrabooks": [
        RequestSpec("GET", "/api/v1/users/me", weight=3),
        RequestSpec("GET", "/api/v1/accounting/accounts", weight=2),
    ],
    "investmitra": [
        RequestSpec("GET", "/api/v1/users/me", weight=3),
        RequestSpec("GET", "/api/v1/investment/holdings?limit=20", weight=3),
    ],
}


@dataclass
class Metric:
    app_key: str
    phase: str
    method: str
    path: str
    status_code: int | None
    ok: bool
    latency_ms: float
    error: str | None


@dataclass
class UserResult:
    user_id: int
    app_key: str
    login_ok: bool
    login_status: int | None
    login_error: str | None


class MetricsStore:
    def __init__(self) -> None:
        self.metrics: list[Metric] = []
        self.users: list[UserResult] = []

    def add_metric(self, metric: Metric) -> None:
        self.metrics.append(metric)

    def add_user(self, user_result: UserResult) -> None:
        self.users.append(user_result)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def choose_operation(app_key: str) -> RequestSpec:
    options = SCENARIOS[app_key]
    weights = [max(1, spec.weight) for spec in options]
    return random.choices(options, weights=weights, k=1)[0]


async def send_request(
    client: httpx.AsyncClient,
    store: MetricsStore,
    *,
    app_key: str,
    phase: str,
    method: str,
    path: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> tuple[httpx.Response | None, Metric]:
    start = time.perf_counter()
    response: httpx.Response | None = None
    error: str | None = None
    status_code: int | None = None

    try:
        response = await client.request(method, path, headers=headers, json=json_body)
        status_code = response.status_code
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000.0
    ok = response is not None and 200 <= response.status_code < 300

    metric = Metric(
        app_key=app_key,
        phase=phase,
        method=method,
        path=path,
        status_code=status_code,
        ok=ok,
        latency_ms=latency_ms,
        error=error,
    )
    store.add_metric(metric)
    return response, metric


async def run_user(
    user_id: int,
    total_users: int,
    client: httpx.AsyncClient,
    store: MetricsStore,
    *,
    email: str,
    password: str,
    tenant_id: str,
    ramp_seconds: float,
    ops_per_user: int,
    think_time_ms: int,
) -> None:
    app_key = APP_KEYS[user_id % len(APP_KEYS)]
    delay = 0.0
    if total_users > 1 and ramp_seconds > 0:
        delay = (user_id / (total_users - 1)) * ramp_seconds
    if delay > 0:
        await asyncio.sleep(delay)

    common_headers = {
        "X-App-Key": app_key,
        "X-Tenant-ID": tenant_id,
    }

    login_response, login_metric = await send_request(
        client,
        store,
        app_key=app_key,
        phase="login",
        method="POST",
        path="/api/v1/auth/login",
        headers={**common_headers, "Content-Type": "application/json"},
        json_body={"email": email, "password": password},
    )

    if not login_response or not login_metric.ok:
        store.add_user(
            UserResult(
                user_id=user_id,
                app_key=app_key,
                login_ok=False,
                login_status=login_metric.status_code,
                login_error=login_metric.error,
            )
        )
        return

    token_data: dict[str, Any] = {}
    try:
        token_data = login_response.json()
    except Exception:  # noqa: BLE001
        pass

    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip()

    if not access_token:
        store.add_user(
            UserResult(
                user_id=user_id,
                app_key=app_key,
                login_ok=False,
                login_status=login_metric.status_code,
                login_error="Login response missing access_token",
            )
        )
        return

    store.add_user(
        UserResult(
            user_id=user_id,
            app_key=app_key,
            login_ok=True,
            login_status=login_metric.status_code,
            login_error=None,
        )
    )

    auth_headers = {
        **common_headers,
        "Authorization": f"Bearer {access_token}",
    }

    for _ in range(ops_per_user):
        spec = choose_operation(app_key)
        headers = auth_headers
        body = spec.body

        if spec.method == "POST":
            headers = {**auth_headers, "Content-Type": "application/json"}

        await send_request(
            client,
            store,
            app_key=app_key,
            phase="operation",
            method=spec.method,
            path=spec.path,
            headers=headers,
            json_body=body,
        )

        if think_time_ms > 0:
            await asyncio.sleep(random.uniform(0, think_time_ms) / 1000.0)

    if refresh_token:
        await send_request(
            client,
            store,
            app_key=app_key,
            phase="logout",
            method="POST",
            path="/api/v1/auth/logout",
            headers={**common_headers, "Content-Type": "application/json"},
            json_body={"refresh_token": refresh_token},
        )


def build_summary(store: MetricsStore, started_at: float, finished_at: float) -> dict[str, Any]:
    total_duration_s = max(0.001, finished_at - started_at)
    total_requests = len(store.metrics)
    successes = [m for m in store.metrics if m.ok]
    responses = [m for m in store.metrics if m.status_code is not None]
    failures = [m for m in store.metrics if not m.ok]
    server_errors = [m for m in store.metrics if m.status_code is not None and m.status_code >= 500]
    timeouts = [m for m in store.metrics if m.error and "timed out" in m.error.lower()]

    latencies = [m.latency_ms for m in responses]

    login_total = len(store.users)
    login_ok = sum(1 for u in store.users if u.login_ok)

    per_app: dict[str, dict[str, Any]] = {}
    for app in APP_KEYS:
        app_metrics = [m for m in store.metrics if m.app_key == app]
        app_lat = [m.latency_ms for m in app_metrics if m.status_code is not None]
        app_server_errors = sum(1 for m in app_metrics if m.status_code is not None and m.status_code >= 500)
        app_failures = sum(1 for m in app_metrics if not m.ok)
        per_app[app] = {
            "request_count": len(app_metrics),
            "error_count": app_failures,
            "server_error_count": app_server_errors,
            "p95_ms": percentile(app_lat, 95),
        }

    per_endpoint: dict[str, dict[str, Any]] = {}
    for metric in store.metrics:
        key = f"{metric.method} {metric.path}"
        bucket = per_endpoint.setdefault(
            key,
            {
                "count": 0,
                "ok_count": 0,
                "server_error_count": 0,
                "latencies": [],
            },
        )
        bucket["count"] += 1
        if metric.ok:
            bucket["ok_count"] += 1
        if metric.status_code is not None and metric.status_code >= 500:
            bucket["server_error_count"] += 1
        if metric.status_code is not None:
            bucket["latencies"].append(metric.latency_ms)

    per_endpoint_summary = {
        key: {
            "count": value["count"],
            "ok_rate": (value["ok_count"] / value["count"]) if value["count"] else 0.0,
            "server_error_rate": (value["server_error_count"] / value["count"]) if value["count"] else 0.0,
            "p95_ms": percentile(value["latencies"], 95),
        }
        for key, value in per_endpoint.items()
    }

    recommendations: list[str] = []
    login_success_rate = (login_ok / login_total) if login_total else 0.0
    server_error_rate = (len(server_errors) / total_requests) if total_requests else 0.0
    timeout_rate = (len(timeouts) / total_requests) if total_requests else 0.0
    p95 = percentile(latencies, 95)

    if login_success_rate < 0.98:
        recommendations.append(
            "Login success rate is below 98%; add auth endpoint autoscaling, increase worker count, and tune DB/Mongo connection pools."
        )
    if server_error_rate > 0.01:
        recommendations.append(
            "HTTP 5xx rate is above 1%; profile failing endpoints and add queueing/retry or isolate heavy operations from request thread."
        )
    if timeout_rate > 0.005:
        recommendations.append(
            "Timeout rate is elevated; increase upstream timeouts carefully and add caching for repetitive read endpoints."
        )
    if p95 is not None and p95 > 1500:
        recommendations.append(
            "p95 latency is above 1.5s; optimize hottest queries and add Redis cache for profile/list endpoints."
        )
    if not recommendations:
        recommendations.append("Current burst profile is stable; proceed with canary rollout and keep runtime monitoring enabled.")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": total_duration_s,
        "requests_total": total_requests,
        "requests_per_second": total_requests / total_duration_s,
        "response_success_rate": (len(successes) / total_requests) if total_requests else 0.0,
        "server_error_rate": server_error_rate,
        "timeout_rate": timeout_rate,
        "login_total_users": login_total,
        "login_success_count": login_ok,
        "login_success_rate": login_success_rate,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": p95,
            "p99": percentile(latencies, 99),
            "max": max(latencies) if latencies else None,
        },
        "per_app": per_app,
        "per_endpoint": per_endpoint_summary,
        "recommendations": recommendations,
    }
    return summary


def render_markdown_report(config: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# Unified Backend Load Test Report",
        "",
        "## Scenario",
        f"- Base URL: `{config['base_url']}`",
        f"- Virtual users: `{config['users']}`",
        f"- Ramp seconds: `{config['ramp_seconds']}`",
        f"- Operations/user: `{config['ops_per_user']}`",
        f"- Tenant: `{config['tenant_id']}`",
        f"- App keys: `{', '.join(APP_KEYS)}`",
        "",
        "## Summary",
        f"- Duration: `{summary['duration_seconds']:.2f}s`",
        f"- Total requests: `{summary['requests_total']}`",
        f"- Throughput: `{summary['requests_per_second']:.2f} req/s`",
        f"- Login success: `{summary['login_success_count']}/{summary['login_total_users']}` ({summary['login_success_rate']*100:.2f}%)",
        f"- Response success rate (2xx): `{summary['response_success_rate']*100:.2f}%`",
        f"- Server error rate (5xx): `{summary['server_error_rate']*100:.2f}%`",
        f"- Timeout rate: `{summary['timeout_rate']*100:.2f}%`",
        f"- Latency p50/p95/p99: `{summary['latency_ms']['p50']}` / `{summary['latency_ms']['p95']}` / `{summary['latency_ms']['p99']}` ms",
        "",
        "## Recommendations",
    ]
    for rec in summary["recommendations"]:
        lines.append(f"- {rec}")

    lines.append("")
    lines.append("## Per-App Snapshot")
    for app_key, app_data in summary["per_app"].items():
        lines.append(
            f"- `{app_key}`: requests={app_data['request_count']}, errors={app_data['error_count']}, 5xx={app_data['server_error_count']}, p95={app_data['p95_ms']} ms"
        )

    return "\n".join(lines) + "\n"


async def run_load_test(args: argparse.Namespace) -> dict[str, Any]:
    random.seed(args.seed)

    base_url = args.base_url.rstrip("/")
    timeout = httpx.Timeout(args.timeout_seconds)
    limits = httpx.Limits(max_connections=max(200, args.users * 3), max_keepalive_connections=max(100, args.users))

    store = MetricsStore()

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits) as client:
        if not args.skip_health_check:
            health_resp = await client.get("/health")
            health_resp.raise_for_status()
            health_payload = {}
            try:
                health_payload = health_resp.json()
            except Exception:
                health_payload = {}

            if not args.allow_degraded:
                db_payload = health_payload.get("db") or {}
                mongo_ok = bool((db_payload.get("mongo") or {}).get("ok"))
                postgres_ok = bool((db_payload.get("postgres") or {}).get("ok"))
                if not mongo_ok or not postgres_ok:
                    raise RuntimeError(
                        "Preflight failed: required datastores are unavailable in /health "
                        f"(mongo_ok={mongo_ok}, postgres_ok={postgres_ok}). "
                        "Start required services or rerun with --allow-degraded for diagnostic-only testing."
                    )

        started = time.perf_counter()
        tasks = [
            asyncio.create_task(
                run_user(
                    idx,
                    args.users,
                    client,
                    store,
                    email=args.email,
                    password=args.password,
                    tenant_id=args.tenant_id,
                    ramp_seconds=args.ramp_seconds,
                    ops_per_user=args.ops_per_user,
                    think_time_ms=args.think_time_ms,
                )
            )
            for idx in range(args.users)
        ]
        await asyncio.gather(*tasks)
        finished = time.perf_counter()

    summary = build_summary(store, started, finished)
    return {
        "config": {
            "base_url": base_url,
            "users": args.users,
            "ramp_seconds": args.ramp_seconds,
            "ops_per_user": args.ops_per_user,
            "tenant_id": args.tenant_id,
            "email": args.email,
            "seed": args.seed,
            "timeout_seconds": args.timeout_seconds,
            "think_time_ms": args.think_time_ms,
        },
        "summary": summary,
        "users": [asdict(u) for u in store.users],
        "metrics": [asdict(m) for m in store.metrics],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified backend load test (multi-frontend profile)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="admin@sanmitra.local")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--tenant-id", default="seed-tenant-1")
    parser.add_argument("--users", type=int, default=250)
    parser.add_argument("--ramp-seconds", type=float, default=25.0)
    parser.add_argument("--ops-per-user", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--think-time-ms", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-health-check", action="store_true")
    parser.add_argument("--allow-degraded", action="store_true", help="Allow running when /health reports Mongo/Postgres unavailable")
    parser.add_argument("--report-prefix", default="load-test")
    parser.add_argument("--report-dir", default="logs/load-test")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    result = asyncio.run(run_load_test(args))
    summary = result["summary"]

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = report_dir / f"{args.report_prefix}-{stamp}.json"
    md_path = report_dir / f"{args.report_prefix}-{stamp}.md"

    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(result["config"], summary), encoding="utf-8")

    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    print(f"LOGIN_SUCCESS_RATE={summary['login_success_rate']*100:.2f}%")
    print(f"RESPONSE_SUCCESS_RATE={summary['response_success_rate']*100:.2f}%")
    print(f"SERVER_ERROR_RATE={summary['server_error_rate']*100:.2f}%")
    print(f"P95_MS={summary['latency_ms']['p95']}")


if __name__ == "__main__":
    main()




