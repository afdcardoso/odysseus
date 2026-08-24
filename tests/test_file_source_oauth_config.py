"""Regression coverage for deployment-managed Google Drive OAuth credentials."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse


def _route(path, method):
    from routes.file_source_routes import setup_file_source_routes

    router = setup_file_source_routes(None)
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_google_start_uses_deployment_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "deployment-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "deployment-client-secret")
    monkeypatch.setenv(
        "GOOGLE_DRIVE_OAUTH_REDIRECT_URI",
        "https://example.test/api/file-sources/google/callback",
    )

    created = {}
    monkeypatch.setattr(
        "routes.file_source_routes.create_source",
        lambda owner, provider, display_name, **fields: created.update(
            owner=owner, provider=provider, display_name=display_name, fields=fields
        ) or {"id": "source-1", **created},
    )
    monkeypatch.setattr("routes.file_source_routes.create_oauth_state", lambda *_: "state-1")
    monkeypatch.setattr("routes.file_source_routes.public_source", lambda source: source)

    start = _route("/api/file-sources/google/start", "POST")
    result = start(
        request=SimpleNamespace(),
        payload={
            "display_name": "My Drive",
            "client_id": "attacker-client-id",
            "client_secret": "attacker-secret",
        },
        owner="alice",
    )

    assert "credentials" not in created["fields"]
    query = parse_qs(urlparse(result["authorization_url"]).query)
    assert query["client_id"] == ["deployment-client-id"]


def test_google_drive_refresh_uses_deployment_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "deployment-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "deployment-client-secret")

    response = MagicMock(status_code=200)
    response.json.return_value = {"access_token": "new-access-token", "expires_in": 3600}
    post = MagicMock(return_value=response)
    monkeypatch.setattr("src.file_sources.httpx.post", post)
    monkeypatch.setattr("src.file_sources.save_source", lambda source: source)

    from src.file_sources import GoogleDriveProvider

    source = {
        "id": "source-1",
        "provider": "google_drive",
        "oauth": {
            "refresh_token": "refresh-token",
            "expires_at": 0,
        },
    }
    assert GoogleDriveProvider(source)._token() == "new-access-token"
    payload = post.call_args.kwargs["data"]
    assert payload["client_id"] == "deployment-client-id"
    assert payload["client_secret"] == "deployment-client-secret"
    assert "credentials" not in source
