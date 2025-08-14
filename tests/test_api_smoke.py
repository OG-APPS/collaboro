from fastapi.testclient import TestClient
import orchestrator.api as orch_api
from orchestrator.api import app

def test_health_smoke(monkeypatch):
    # Use in-process app instead of external server
    monkeypatch.setattr(orch_api, "API_TOKEN", "", raising=False)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
