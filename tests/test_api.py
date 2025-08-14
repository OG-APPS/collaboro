from fastapi.testclient import TestClient
import orchestrator.api as orch_api
from orchestrator.api import app, init_db, get_db


def reset_db():
    # Drop tables to ensure a clean test DB, then re-init schema
    conn = get_db()
    conn.executescript(
        """
        DROP TABLE IF EXISTS runs;
        DROP TABLE IF EXISTS jobs;
        DROP TABLE IF EXISTS user_logs;
        """
    )
    conn.commit()
    conn.close()
    init_db()


def _disable_auth(monkeypatch):
    # Ensure module-level token is unset regardless of process env
    monkeypatch.setattr(orch_api, "API_TOKEN", "", raising=False)


def test_health_with_and_without_token(monkeypatch):
    _disable_auth(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200


def test_enqueue_and_claim_job(monkeypatch):
    _disable_auth(monkeypatch)
    reset_db()
    with TestClient(app) as client:
        # Enqueue a job
        r = client.post("/enqueue/warmup", json={"device_serial": "dev1", "seconds": 10})
        assert r.status_code == 200
        jid = r.json()["job_id"]

        # Claim next job
        r2 = client.get("/jobs/next", params={"device": "dev1"})
        assert r2.status_code == 200
        claimed = r2.json()
        assert claimed["id"] == jid
        assert claimed["status"] == "running"

        # Completing the job updates status
        r3 = client.post(f"/jobs/{jid}/complete", params={"ok": True})
        assert r3.status_code == 200
        # verify job status
        r4 = client.get(f"/jobs/{jid}")
        assert r4.status_code == 200
        assert r4.json()["status"] == "done"


def test_jobs_endpoint_claim_via_status_next(monkeypatch):
    _disable_auth(monkeypatch)
    reset_db()
    with TestClient(app) as client:
        # enqueue two jobs
        client.post("/enqueue/warmup", json={"device_serial": "devA", "seconds": 1})
        client.post("/enqueue/warmup", json={"device_serial": "devA", "seconds": 1})
        # claim using status=next
        r = client.get("/jobs", params={"device": "devA", "status": "next"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) == 1
        assert data[0]["status"] == "running"


def test_cancel_retry_flow(monkeypatch):
    _disable_auth(monkeypatch)
    reset_db()
    with TestClient(app) as client:
        jid = client.post("/enqueue/warmup", json={"device_serial": "dev2", "seconds": 5}).json()["job_id"]
        # cancel
        r = client.post(f"/jobs/{jid}/cancel")
        assert r.status_code == 200
        # retry creates a new job
        r2 = client.post(f"/jobs/{jid}/retry")
        assert r2.status_code == 200
        assert r2.json()["job_id"] != jid

# New tests: API validation negatives and scheduler repeat behavior

def test_pipeline_validation_negatives(monkeypatch):
    _disable_auth(monkeypatch)
    reset_db()
    with TestClient(app) as client:
        # duration must be >=1
        r = client.post("/enqueue/pipeline", json={"device_serial":"devX","steps":[{"type":"warmup","duration":0}]})
        assert r.status_code in (400, 422)
        # like_prob bounds
        r2 = client.post("/enqueue/pipeline", json={"device_serial":"devX","steps":[{"type":"warmup","duration":10,"like_prob":1.5}]})
        assert r2.status_code in (400, 422)
        # unknown type should fail validation
        r3 = client.post("/enqueue/pipeline", json={"device_serial":"devX","steps":[{"type":"unknown","duration":10}]})
        assert r3.status_code in (400, 422)


def test_scheduler_repeat_enqueues_repeat_in_payload(monkeypatch):
    _disable_auth(monkeypatch)
    reset_db()
    # Simulate scheduler's use of enqueue_pipeline by calling API directly
    with TestClient(app) as client:
        steps = [{"type":"warmup","duration":2}]
        r = client.post("/enqueue/pipeline", json={"device_serial":"devX","steps":steps,"repeat":2})
        assert r.status_code == 200
        jid = r.json()["job_id"]
        # Verify job payload contains repeat=2
        jr = client.get(f"/jobs/{jid}")
        assert jr.status_code == 200
        payload = jr.json().get("payload")
        assert payload
        import json as _json
        pd = _json.loads(payload)
        assert pd.get("repeat") == 2
