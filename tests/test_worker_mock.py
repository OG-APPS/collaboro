import types
from worker.device_worker import _make_should_continue


def test_make_should_continue_handles_api_errors(monkeypatch):
    # Simulate requests.get raising an exception; should_continue returns True in that case
    calls = {"count": 0}
    def fake_get(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("network error")

    import worker.device_worker as w
    monkeypatch.setattr(w.requests, "get", fake_get)

    should_continue = _make_should_continue(123)
    assert should_continue() is True
    assert calls["count"] == 1

