from fastapi.testclient import TestClient
import orchestrator.api as orch_api
from orchestrator.api import app
import sys, types


def test_devices_no_crash(monkeypatch):
    # disable auth
    monkeypatch.setattr(orch_api, "API_TOKEN", "", raising=False)

    # make subprocess adb fail
    def _fail(*args, **kwargs):
        raise RuntimeError("adb error")
    monkeypatch.setattr(orch_api.subprocess, "check_output", _fail)

    # make adbutils import succeed but device_list fail
    fake_mod = types.SimpleNamespace()
    fake_mod.adb = types.SimpleNamespace(device_list=lambda: (_ for _ in ()).throw(RuntimeError("adbutils error")))
    monkeypatch.setitem(sys.modules, "adbutils", fake_mod)

    with TestClient(app) as client:
        r = client.get("/devices")
        assert r.status_code == 200
        assert r.json() == []

