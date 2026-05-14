from app.main import app


def _route_keys() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    }


def test_gruhamitra_dashboard_and_flat_aliases_are_registered():
    routes = _route_keys()

    assert ("GET", "/api/v1/dashboard/summary") in routes
    assert ("GET", "/api/v1/flats") in routes


def test_gruhamitra_frontend_aliases_are_registered():
    routes = _route_keys()

    expected = {
        ("POST", "/api/v1/journal"),
        ("PUT", "/api/v1/auth/me"),
        ("POST", "/api/v1/database/backup-on-logout"),
        ("GET", "/api/v1/users/"),
        ("PATCH", "/api/v1/users/{user_id}/role"),
        ("GET", "/api/v1/member-onboarding"),
        ("GET", "/api/v1/member-onboarding/debug"),
        ("GET", "/api/v1/member-onboarding/my-profile"),
        ("GET", "/api/v1/society/{society_id}"),
        ("POST", "/api/v1/v2/societies/{society_id}/join-requests"),
    }

    assert expected <= routes


def test_gruhamitra_file_and_import_compat_routes_are_registered():
    routes = _route_keys()

    expected = {
        ("POST", "/api/v1/attachments/upload/{journal_entry_id}"),
        ("GET", "/api/v1/attachments/journal/{journal_entry_id}"),
        ("GET", "/api/v1/attachments/{attachment_id}"),
        ("DELETE", "/api/v1/attachments/{attachment_id}"),
        ("POST", "/api/v1/resources/files/upload"),
        ("GET", "/api/v1/resources/files"),
        ("GET", "/api/v1/resources/files/{file_id}"),
        ("GET", "/api/v1/resources/files/{file_id}/download"),
        ("DELETE", "/api/v1/resources/files/{file_id}"),
        ("GET", "/api/v1/onboarding-imports/templates/{kind}.csv"),
        ("POST", "/api/v1/onboarding-imports/demo/import"),
        ("POST", "/api/v1/onboarding-imports/import/flats"),
        ("POST", "/api/v1/onboarding-imports/import/members"),
    }

    assert expected <= routes
