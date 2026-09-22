from fastapi.testclient import TestClient

from gametheory.api import app
from gametheory.config import Settings, get_settings


def test_unconfigured_auth_fails_explicitly_without_sql(monkeypatch):
    settings = Settings(_env_file=None)
    monkeypatch.setattr("gametheory.api.get_settings", lambda: settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.get("/api/config")
            assert response.status_code == 200
            assert response.json()["auth"]["configured"] is False
            assert response.json()["capabilities"]["execution"] is False
            assert client.get("/api/workspaces").status_code == 503
            assert client.get("/api/missing").status_code == 404
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.clear()
