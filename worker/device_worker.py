from __future__ import annotations
import time, requests, os, json
from typing import Callable
from loguru import logger
from utils.logger_setup import setup_logger
from core.device_runner import DeviceRunner

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
DEVICE = os.environ.get("DEVICE_SERIAL", "")
TOKEN = os.environ.get("API_TOKEN", "").strip()
HEADERS = ({"X-API-Token": TOKEN} if TOKEN else {})

def _make_should_continue(jid: int) -> Callable[[], bool]:
    """Returns a function that checks if the job is still allowed to run.
    Treats statuses other than running/queued as stop signals (cancelled/paused/failed/done)."""
    def _inner() -> bool:
        try:
            rr = requests.get(f"{API}/jobs/{jid}", timeout=5, headers=HEADERS)
            if rr.status_code != 200:
                return True
            row = rr.json()
            return row.get("status") in ("running", "queued")
        except Exception:
            return True
    return _inner

def run():
    setup_logger("worker")
    if not DEVICE:
        logger.error("DEVICE_SERIAL not set")
        return
    dr = DeviceRunner(DEVICE)
    logger.info(f"Worker starting for device {DEVICE}")
    backoff = 1.0
    while True:
        try:
            r = requests.get(f"{API}/jobs/next", params={"device": DEVICE}, timeout=10, headers=HEADERS)
            if r.status_code != 200:
                time.sleep(min(backoff, 10.0)); backoff = min(backoff * 2, 10.0); continue
            backoff = 1.0
            j = r.json() or {}
            if not j:
                time.sleep(1.0); continue
            jid = j.get("id"); jtype = j.get("type")
            try:
                payload = json.loads(j.get("payload") or "{}")
            except json.JSONDecodeError as de:
                logger.error(f"Job {jid}: invalid payload JSON: {de}"); payload = {}
            ok = True
            logger.info(f"Running job {jid} type={jtype}")

            should_continue = _make_should_continue(jid)

            if jtype == "warmup":
                secs = int(payload.get("seconds", 60))
                likep = float(payload.get("like_prob", 0.07))
                ok = dr.warmup(seconds=secs, like_prob=likep, should_continue=should_continue)
            elif jtype == "pipeline":
                ok = dr.run_pipeline(payload, should_continue=should_continue)
            else:
                logger.warning(f"Job {jid}: Unknown job type: {jtype}"); ok = False
            try:
                requests.post(f"{API}/jobs/{jid}/complete", params={"ok": ok}, timeout=10, headers=HEADERS)
            except Exception as e:
                logger.warning(f"Job {jid}: Could not notify completion: {e}")
        except requests.RequestException as e:
            logger.error(f"Network error polling jobs: {e}"); time.sleep(min(backoff, 10.0)); backoff = min(backoff * 2, 10.0)
        except Exception as e:
            logger.error(f"Loop error: {e}"); time.sleep(2.0)

if __name__ == "__main__": run()
