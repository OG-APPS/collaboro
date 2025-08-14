from __future__ import annotations
import time, os, requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from utils.logger_setup import setup_logger
from utils.config import load_config

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("API_TOKEN", "").strip()
HEADERS = ({"X-API-Token": TOKEN} if TOKEN else {})

def enqueue_pipeline(device: str, steps, repeat=1):
    try:
        r=requests.post(f"{API}/enqueue/pipeline", json={
            "device_serial": device,
            "steps": steps,
            "repeat": repeat,
            "sleep_between": [2,5]
        }, timeout=10, headers=HEADERS)
        logger.info(f"Enqueued pipeline status={r.status_code}")
    except Exception as e:
        logger.error(f"enqueue error: {e}")

def build_steps_for_schedule(cfg, items):
    out=[]
    cycles = cfg.get("cycles", {})
    for it in items or []:
        if it.get("type") == "cycle":
            name = it.get("name","")
            out.extend(cycles.get(name, {}).get("steps", []))
        elif it.get("type") == "break":
            out.append({"type":"break", "duration": int(it.get("minutes",10))*60})
    return out

def schedule_jobs(sched: BackgroundScheduler, cfg):
    device = cfg.get("system", {}).get("default_device", "")
    schedules = cfg.get("schedules", {})
    for sname, sdata in schedules.items():
        items = sdata.get("items", [])
        repeat = int(sdata.get("repeat", 1))
        times = sdata.get("start_times", [])
        def run_schedule(items=items, device=device, repeat=repeat, cfg=cfg, sname=sname):
            logger.info(f"Running schedule: {sname}")
            steps = build_steps_for_schedule(cfg, items)
            if not steps:
                logger.warning("Schedule has no steps.")
                return
            # Use configured repeat instead of hardcoded value
            enqueue_pipeline(device, steps, repeat=repeat)
        if not times:
            sched.add_job(run_schedule, "interval", minutes=60, id=f"sched_{sname}", replace_existing=True)
        else:
            for ts in times:
                try:
                    hh, mm = ts.split(":")
                    trig = CronTrigger(hour=int(hh), minute=int(mm))
                    sched.add_job(run_schedule, trig, id=f"sched_{sname}_{ts}", replace_existing=True)
                except Exception as e:
                    logger.error(f"Invalid start_time '{ts}' in schedule '{sname}': {e}")

def run_scheduler_loop():
    setup_logger("scheduler")
    cfg = load_config()
    logger.info("Scheduler loop started (no blocking sleeps).")
    sched = BackgroundScheduler()
    schedule_jobs(sched, cfg)
    
    def _resync():
        try:
            new_cfg = load_config()
            # Clear existing schedule jobs (ids starting with 'sched_')
            for job in list(sched.get_jobs()):
                try:
                    if job.id.startswith("sched_"):
                        sched.remove_job(job.id)
                except Exception:
                    continue
            schedule_jobs(sched, new_cfg)
            logger.info("Scheduler synced with config")
        except Exception as e:
            logger.warning(f"Scheduler resync failed: {e}")

    # Run resync every 30 seconds to reflect config changes and remove old jobs
    sched.add_job(_resync, "interval", seconds=30, id="sched_resync", replace_existing=True)
    
    sched.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        sched.shutdown()

if __name__=="__main__": run_scheduler_loop()
