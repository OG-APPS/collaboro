from __future__ import annotations
import os, sqlite3, json, pathlib, time, subprocess
from typing import Optional, Dict, Any, List, Tuple, Literal
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Body, Depends, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, model_validator
from loguru import logger
from utils.logger_setup import setup_logger
from utils.config import load_config, save_config
from utils.user_logger import user_logger

DB_PATH = os.environ.get("DB_PATH", "artifacts/orchestrator.db")
LOG_DIR = os.environ.get("LOG_DIR", "artifacts/logs")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def verify_token(request: Request, x_api_token: Optional[str] = Header(default=None)) -> bool:
    """Basic header token check. If API_TOKEN is unset, no auth is required.
    Logs unauthorized attempts without revealing token values.
    """
    if not API_TOKEN:
        return True
    if x_api_token != API_TOKEN:
        client = getattr(request.client, "host", "?") if getattr(request, "client", None) else "?"
        logger.warning(f"Unauthorized API request from {client} to {request.url.path}; token provided: {bool(x_api_token)}")
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      device TEXT, type TEXT, payload TEXT,
      status TEXT DEFAULT 'queued', created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id INTEGER, device TEXT, status TEXT,
      started_at DATETIME DEFAULT CURRENT_TIMESTAMP, ended_at DATETIME);
    -- Accounts & Sessions
    CREATE TABLE IF NOT EXISTS accounts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE,
      status TEXT DEFAULT 'new',
      health INTEGER DEFAULT 100,
      proxy_label TEXT,
      last_login_at DATETIME,
      last_used_at DATETIME,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS credentials(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
      login TEXT, password TEXT,
      twofa TEXT, recovery TEXT
    );
    CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
      device TEXT, started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      ended_at DATETIME, status TEXT, notes TEXT
    );
    CREATE TABLE IF NOT EXISTS posts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
      video_path TEXT, caption_hash TEXT,
      posted_at DATETIME, success INTEGER, tiktok_id TEXT, error TEXT
    );
    CREATE TABLE IF NOT EXISTS proxies(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label TEXT UNIQUE, type TEXT, host TEXT, port INTEGER, username TEXT, password TEXT,
      last_checked DATETIME
    );
    -- Indices
    CREATE INDEX IF NOT EXISTS idx_jobs_device_status_id ON jobs(device, status, id);
    CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id);
    CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
    CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);
    """)
    conn.commit(); conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    pathlib.Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    setup_logger("api", LOG_DIR); init_db()
    logger.info("API starting…"); logger.info(f"Database ready at {DB_PATH}")
    if not API_TOKEN:
        logger.warning("API TOKEN not set; API is unsecured (localhost-only recommended)")
    yield
    logger.info("API stopping…")

class PipelineStep(BaseModel):
    type: Literal["warmup", "break", "post_video", "rotate_identity", "close_app", "login", "log_account_data"]
    duration: Optional[int] = Field(default=None, ge=1, le=24*60*60)
    like_prob: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    video: Optional[str] = None
    caption: Optional[str] = None

class EnqueueWarmup(BaseModel):
    device_serial: str
    seconds: int = Field(default=60, ge=1, le=24*60*60)
    like_prob: float = Field(default=0.07, ge=0.0, le=1.0)

class EnqueuePipeline(BaseModel):
    device_serial: str
    steps: List[PipelineStep] = []
    repeat: int = Field(default=1, ge=1, le=100)
    sleep_between: List[int] = Field(default_factory=lambda: [2, 5])


app = FastAPI(title="Automation API", version="1.4", lifespan=lifespan)



def row_to_dict(r: sqlite3.Row) -> Dict[str, Any]: return {k:r[k] for k in r.keys()}
def enqueue_job(device: str, jtype: str, payload: Dict[str, Any]) -> int:
    conn=get_db(); cur=conn.execute("INSERT INTO jobs(device,type,payload,status) VALUES(?,?,?,?)",(device,jtype,json.dumps(payload),"queued"))
    jid=cur.lastrowid; conn.commit(); conn.close(); logger.info(f"Enqueued job {jid} type={jtype} device={device}"); return jid

@app.get("/health")
def health(_: bool = Depends(verify_token)): return {"ok": True, "ts": time.time()}

def _adb_model(serial: str) -> str:
    try:
        out = subprocess.check_output(["adb","-s",serial,"shell","getprop","ro.product.model"], text=True)
        return out.strip()
    except Exception:
        return ""

def _adb_android_version(serial: str) -> str:
    try:
        out = subprocess.check_output(["adb","-s",serial,"shell","getprop","ro.build.version.release"], text=True)
        return out.strip()
    except Exception:
        return ""

@app.get("/devices")
def devices(_: bool = Depends(verify_token), request: Request = None):
    devs_map: Dict[str, Dict[str, Any]] = {}
    client = getattr(request.client, "host", "?") if request and getattr(request, "client", None) else "?"
    # Try subprocess `adb devices -l`
    try:
        out = subprocess.check_output(["adb","devices","-l"], text=True, stderr=subprocess.STDOUT)
        for line in out.splitlines():
            line=line.strip()
            if not line or line.startswith("List of devices"): continue
            parts=line.split()
            serial=parts[0]
            state="unknown"
            for p in parts[1:]:
                if p in ("device","offline","unauthorized"): state=p
            devs_map[serial] = {
                "serial": serial,
                "state": state,
                "model": _adb_model(serial),
                "android": _adb_android_version(serial)
            }
    except Exception as e:
        logger.warning(f"/devices: adb devices -l failed from {client}: {e}")
    # Also union with adbutils list (if available)
    try:
        import adbutils  # type: ignore
        for d in adbutils.adb.device_list():
            if d.serial not in devs_map:
                devs_map[d.serial] = {
                    "serial": d.serial,
                    "state": "device",
                    "model": _adb_model(d.serial),
                    "android": _adb_android_version(d.serial)
                }
    except Exception as e:
        logger.warning(f"/devices: adbutils listing failed from {client}: {e}")
    logger.info(f"/devices: {len(devs_map)} device(s) visible to API from {client}")
    return list(devs_map.values())

@app.get("/debug/adb")
def debug_adb(_: bool = Depends(verify_token), request: Request = None):
    client = getattr(request.client, "host", "?") if request and getattr(request, "client", None) else "?"
    adb_text = None; adb_err = None; adbutils_err = None; adbutils_list: List[str] = []
    try:
        adb_text = subprocess.check_output(["adb","devices","-l"], text=True, stderr=subprocess.STDOUT)
    except Exception as e:
        adb_err = str(e)
    try:
        import adbutils  # type: ignore
        adbutils_list = [d.serial for d in adbutils.adb.device_list()]
    except Exception as e:
        adbutils_err = str(e)
    eff = [d.get("serial") for d in devices(True, request=request)]  # type: ignore
    logger.info(f"/debug/adb from {client}: adb_ok={adb_text is not None}, adbutils_ok={not bool(adbutils_err)}; effective={eff}")
    return {"adb_output": adb_text, "adb_error": adb_err, "adbutils_list": adbutils_list, "adbutils_error": adbutils_err, "effective_devices": eff}

@app.get("/jobs")
def get_jobs(device: Optional[str] = None, status: Optional[str] = None, _: bool = Depends(verify_token)):
    conn=get_db(); q="SELECT * FROM jobs WHERE 1=1"; params=[]
    if device: q+=" AND device=?"; params.append(device)
    if status and status!="next": q+=" AND status=?"; params.append(status)
    q+=" ORDER BY id DESC LIMIT 500"
    rows=[row_to_dict(r) for r in conn.execute(q,params).fetchall()]; conn.close()
    if device and status=="next":
        conn=get_db()
        try:
            r = conn.execute(
                """
                UPDATE jobs
                SET status='running'
                WHERE id = (
                  SELECT id FROM jobs
                  WHERE device=? AND status='queued'
                  ORDER BY id ASC LIMIT 1
                )
                RETURNING id, device, type, payload, status, created_at
                """,
                (device,),
            ).fetchone()
            if not r: conn.close(); return []
            jid=r["id"]
            conn.execute("INSERT INTO runs(job_id,device,status) VALUES(?,?,?)", (jid, device, "running"))
            conn.commit(); rr=row_to_dict(r); conn.close(); return [rr]
        except Exception:
            # Fallback transaction
            try:
                conn.execute("BEGIN IMMEDIATE")
                r=conn.execute("SELECT * FROM jobs WHERE device=? AND status='queued' ORDER BY id ASC LIMIT 1",(device,)).fetchone()
                if not r:
                    conn.execute("COMMIT"); conn.close(); return []
                jid=r["id"]
                conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
                conn.execute("INSERT INTO runs(job_id,device,status) VALUES(?,?,?)", (jid, device, "running"))
                conn.execute("COMMIT"); rr=row_to_dict(r); conn.close(); return [rr]
            except Exception:
                try: conn.execute("ROLLBACK")
                except Exception: pass
                conn.close(); return []
    return rows

@app.get("/jobs/next")
def get_next_job(device: str, _: bool = Depends(verify_token)):
    """Atomically claim the next queued job for a device and mark it running.
    Uses a single UPDATE ... RETURNING statement (SQLite 3.35+) to avoid races.
    """
    conn = get_db()
    try:
        r = conn.execute(
            """
            UPDATE jobs
            SET status='running'
            WHERE id = (
              SELECT id FROM jobs
              WHERE device=? AND status='queued'
              ORDER BY id ASC LIMIT 1
            )
            RETURNING id, device, type, payload, status, created_at
            """,
            (device,),
        ).fetchone()
        if not r:
            conn.close(); return {}
        jid = r["id"]
        conn.execute("INSERT INTO runs(job_id,device,status) VALUES(?,?,?)", (jid, device, "running"))
        conn.commit(); rr=row_to_dict(r); conn.close(); return rr
    except Exception:
        # Fallback: wrap SELECT+UPDATE in a transaction for older SQLite versions
        try:
            conn.execute("BEGIN IMMEDIATE")
# --- Accounts API ---
class AccountIn(BaseModel):
    username: str
    password: Optional[str] = None
    twofa: Optional[str] = None
    recovery: Optional[str] = None
    proxy_label: Optional[str] = None
    status: Optional[str] = None  # new, verified, warmup, active, suspended, banned

@app.post("/accounts/import")
def import_accounts(items: List[AccountIn], _: bool = Depends(verify_token)):
    conn = get_db(); cur = conn.cursor(); created=0; updated=0
    for it in items:
        # upsert by username
        r = cur.execute("SELECT id FROM accounts WHERE username=?", (it.username,)).fetchone()
        if r:
            acc_id = int(r[0]); updated += 1
            if it.proxy_label: cur.execute("UPDATE accounts SET proxy_label=? WHERE id=?", (it.proxy_label, acc_id))
            if it.status: cur.execute("UPDATE accounts SET status=? WHERE id=?", (it.status, acc_id))
            if any([it.password, it.twofa, it.recovery]):
                # replace credentials
                cur.execute("DELETE FROM credentials WHERE account_id=?", (acc_id,))
                cur.execute("INSERT INTO credentials(account_id,login,password,twofa,recovery) VALUES(?,?,?,?,?)",
                            (acc_id, it.username, it.password, it.twofa, it.recovery))
        else:
            cur.execute("INSERT INTO accounts(username,status,proxy_label) VALUES(?,?,?)",
                        (it.username, it.status or 'new', it.proxy_label))
            acc_id = cur.lastrowid; created += 1
            cur.execute("INSERT INTO credentials(account_id,login,password,twofa,recovery) VALUES(?,?,?,?,?)",
                        (acc_id, it.username, it.password, it.twofa, it.recovery))
    conn.commit(); conn.close(); return {"created": created, "updated": updated}

@app.get("/accounts")
def list_accounts(status: Optional[str] = Query(None), limit: int = Query(100), _: bool = Depends(verify_token)):
    conn = get_db(); q = "SELECT * FROM accounts"; params=[]
    if status:
        q += " WHERE status=?"; params.append(status)
    q += " ORDER BY last_used_at NULLS FIRST, id DESC LIMIT ?"; params.append(int(limit))
    rows=[row_to_dict(r) for r in conn.execute(q, params).fetchall()]; conn.close(); return rows

class AssignRequest(BaseModel):
    cooldown_s: int = 300
    status_pool: List[str] = ["verified","warmup","active"]
    proxy_required: bool = False

@app.post("/accounts/assign")
def assign_account(req: AssignRequest, _: bool = Depends(verify_token)):
    now = int(time.time())
    conn = get_db(); cur = conn.cursor()
    # pick account by status, not used within cooldown
    q = ("SELECT * FROM accounts WHERE status IN (%s) AND (last_used_at IS NULL OR strftime('%s', 'now') - strftime('%s', COALESCE(last_used_at, '1970-01-01')) > ?) "
         "ORDER BY COALESCE(last_used_at, '1970-01-01') ASC LIMIT 1") % (",".join(["?"]*len(req.status_pool)))
    row = cur.execute(q, list(req.status_pool)+[int(req.cooldown_s)]).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="No account available")
    acc = row_to_dict(row)
    cred = cur.execute("SELECT * FROM credentials WHERE account_id=?", (acc["id"],)).fetchone()
    if not cred:
        conn.close(); raise HTTPException(status_code=409, detail="Account missing credentials")
    # mark last_used_at now
    cur.execute("UPDATE accounts SET last_used_at=CURRENT_TIMESTAMP WHERE id=?", (acc["id"],))
    conn.commit(); conn.close()
    out = {"account": acc, "credentials": row_to_dict(cred)}
    return out

            r = conn.execute(
                "SELECT * FROM jobs WHERE device=? AND status='queued' ORDER BY id ASC LIMIT 1",
                (device,),
            ).fetchone()
            if not r:
                conn.execute("COMMIT"); conn.close(); return {}
            jid = r["id"]
            conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
            conn.execute("INSERT INTO runs(job_id,device,status) VALUES(?,?,?)", (jid, device, "running"))
            conn.execute("COMMIT")
            rr = row_to_dict(r); conn.close(); return rr
        except Exception:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            conn.close(); return {}


@app.get("/runs")
def get_runs(device: Optional[str] = None, job_id: Optional[int] = None, _: bool = Depends(verify_token)):
    conn=get_db(); q="SELECT * FROM runs WHERE 1=1"; params=[]
    if device: q+=" AND device=?"; params.append(device)
    if job_id: q+=" AND job_id=?"; params.append(job_id)
    q+=" ORDER BY id DESC LIMIT 500"
    rows=[row_to_dict(r) for r in conn.execute(q,params).fetchall()]; conn.close(); return rows

@app.post("/enqueue/warmup")
def post_enqueue_warmup(req: EnqueueWarmup, _: bool = Depends(verify_token)):
    payload = {"seconds": req.seconds, "like_prob": req.like_prob}
    jid = enqueue_job(req.device_serial, "warmup", payload)
    return {"job_id": jid}

@app.post("/enqueue/pipeline")
def post_enqueue_pipeline(req: EnqueuePipeline, _: bool = Depends(verify_token)):
    # Ensure steps are JSON-serializable (convert models to dicts)
    steps_payload: List[Dict[str, Any]] = []
    try:
        for st in req.steps:
            if isinstance(st, BaseModel):
                steps_payload.append(st.model_dump())
            else:
                steps_payload.append(dict(st))  # type: ignore[arg-type]
    except Exception as e:
        logger.error(f"Invalid pipeline steps: {e}")
        raise HTTPException(400, f"Invalid steps: {e}")
    payload = {"steps": steps_payload, "repeat": req.repeat, "sleep_between": req.sleep_between}
    return {"job_id": enqueue_job(req.device_serial, "pipeline", payload)}

@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, _: bool = Depends(verify_token)):
    conn=get_db(); r=conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r: conn.close(); raise HTTPException(404, "Job not found")
    if r["status"] in ("done","failed","cancelled"): conn.close(); return {"ok": True}
    conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
    conn.execute("UPDATE runs SET status='cancelled', ended_at=CURRENT_TIMESTAMP WHERE job_id=? AND ended_at IS NULL", (job_id,))
    conn.commit(); conn.close(); logger.info(f"Job {job_id} cancelled"); return {"ok": True}

@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, _: bool = Depends(verify_token)):
    conn=get_db(); r=conn.execute("SELECT device,type,payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r: conn.close(); raise HTTPException(404, "Job not found")
    new_id = enqueue_job(r["device"], r["type"], json.loads(r["payload"] or "{}"))
    return {"job_id": new_id}

@app.post("/jobs/{job_id}/complete")
def complete_job(job_id: int, ok: bool = True, _: bool = Depends(verify_token)):
    conn=get_db()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", ("done" if ok else "failed", job_id))
    conn.execute("UPDATE runs SET status=?, ended_at=CURRENT_TIMESTAMP WHERE job_id=? AND ended_at IS NULL", ("done" if ok else "failed", job_id))
    conn.commit(); conn.close(); return {"ok": True}

@app.get("/jobs/{job_id}")
def get_job(job_id: int, _: bool = Depends(verify_token)):
    conn=get_db(); r=conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(); conn.close()
    if not r: raise HTTPException(404, "Job not found")
    return row_to_dict(r)

@app.get("/logs")
def get_logs(source: str = Query("all"), lines: int = Query(1000), _: bool = Depends(verify_token)):
    # Clamp lines to avoid excessive IO
    try:
        lines = max(1, min(int(lines), 5000))
    except Exception:
        lines = 1000
    files=[]
    if source in ("all","api"): files.append(pathlib.Path(LOG_DIR)/"api.log")
    if source in ("all","worker"): files.append(pathlib.Path(LOG_DIR)/"worker.log")
    if source in ("all","scheduler"): files.append(pathlib.Path(LOG_DIR)/"scheduler.log")
    chunks=[]
    for f in files:
        if f.exists():
            text=f.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:]
            chunks.append(f"===== {f.name} =====\n" + "\n".join(text))
    return PlainTextResponse("\n\n".join(chunks) if chunks else "(no logs)")

@app.get("/logs/user")
def get_user_logs(device: str = Query(None), limit: int = Query(100), _: bool = Depends(verify_token)):
    """Get user-friendly automation logs."""
    logs = user_logger.get_recent_logs(device=device, limit=limit)
    logger.debug(f"API /logs/user: device={device}, limit={limit}, returning {len(logs)} logs")
    if logs:
        logger.debug(f"API /logs/user: first log = {logs[0]}")
    return {"logs": logs}

@app.delete("/logs/user")
def clear_user_logs(device: str = Query(None), _: bool = Depends(verify_token)):
    """Clear user-friendly logs. If device is provided, clears only that device's logs; otherwise clears all."""
    user_logger.clear_logs(device=device)
    return {"ok": True}

@app.get("/metrics")
def get_metrics(_: bool = Depends(verify_token)):
    """Basic job status counters and device assignments."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT status, COUNT(1) AS cnt FROM jobs GROUP BY status").fetchall()
        by_status = {r["status"]: r["cnt"] for r in rows}
        rows2 = conn.execute("SELECT device, COUNT(1) AS cnt FROM jobs GROUP BY device").fetchall()
        by_device = {r["device"]: r["cnt"] for r in rows2}
        rows3 = conn.execute("SELECT COUNT(1) FROM runs").fetchone()
        total_runs = int(rows3[0]) if rows3 else 0
        return {"jobs_by_status": by_status, "jobs_by_device": by_device, "total_runs": total_runs}
    finally:
        conn.close()

@app.post("/debug/screen")
def debug_screen_state(device_serial: str = Body(..., embed=True), _: bool = Depends(verify_token)):
    """Debug current screen state for a device."""
    try:
        from interfaces.ui2 import connect
        from interfaces.screen_monitor import ScreenMonitor

        d = connect(device_serial)
        monitor = ScreenMonitor(d, device_serial)

        # Force print screen state
        monitor.print_screen_state(force=True)

        # Get data for API response
        texts = monitor.get_visible_text()
        clickables = monitor.get_clickable_elements()
        page_type = monitor.detect_page_type(texts)
        suggestions = monitor.get_suggested_actions(page_type, texts)

        return {
            "device": device_serial,
            "page_type": page_type,
            "visible_text": texts,
            "clickable_elements": clickables,
            "suggestions": suggestions,
            "is_stuck": monitor.is_stuck()
        }
    except Exception as e:
        logger.error(f"Screen debug failed: {e}")
        raise HTTPException(500, f"Screen debug failed: {e}")

@app.post("/debug/click-generic-ads")
def click_generic_ads(device_serial: str = Body(..., embed=True), _: bool = Depends(verify_token)):
    """Click on 'Generic ads' option in TikTok ads preferences popup."""
    try:
        from interfaces.ui2 import connect
        from utils.user_logger import user_logger

        d = connect(device_serial)

        # Look for "Generic ads" text and click it
        try:
            # Try to find and click "Generic ads" element
            generic_ads_elem = d.xpath('//*[contains(@text, "Generic ads")]').get()
            if generic_ads_elem:
                generic_ads_elem.click()
                user_logger.popup_dismissed(device_serial, "Selected Generic ads")
                time.sleep(0.5)

                # Now look for "Select" button and click it
                select_elem = d.xpath('//*[contains(@text, "Select")]').get()
                if select_elem:
                    select_elem.click()
                    user_logger.popup_dismissed(device_serial, "Ads preferences completed")
                    return {"ok": True, "action": "Clicked Generic ads and Select"}
                else:
                    return {"ok": True, "action": "Clicked Generic ads, but Select button not found"}
            else:
                return {"ok": False, "error": "Generic ads option not found"}

        except Exception as e:
            logger.error(f"Failed to click generic ads: {e}")
            return {"ok": False, "error": str(e)}

    except Exception as e:
        logger.error(f"Click generic ads failed: {e}")
        raise HTTPException(500, f"Click generic ads failed: {e}")

@app.post("/debug/deny-contact-sync")
def deny_contact_sync(device_serial: str = Body(..., embed=True), _: bool = Depends(verify_token)):
    """Click 'Don't allow' on TikTok contact sync permission popup."""
    try:
        from interfaces.ui2 import connect
        from utils.user_logger import user_logger

        d = connect(device_serial)

        # Look for "Don't allow" button and click it
        try:
            dont_allow_elem = d.xpath('//*[contains(@text, "Don\'t allow")]').get()
            if dont_allow_elem:
                dont_allow_elem.click()
                user_logger.popup_dismissed(device_serial, "Contact sync denied")
                return {"ok": True, "action": "Clicked Don't allow for contact sync"}
            else:
                return {"ok": False, "error": "Don't allow button not found"}

        except Exception as e:
            logger.error(f"Failed to deny contact sync: {e}")
            return {"ok": False, "error": str(e)}

    except Exception as e:
        logger.error(f"Deny contact sync failed: {e}")
        raise HTTPException(500, f"Deny contact sync failed: {e}")

@app.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: int, lines: int = Query(200), _: bool = Depends(verify_token)):
    p = pathlib.Path(LOG_DIR)
    files = [p/"api.log", p/"worker.log", p/"scheduler.log"]
    patterns = [f"job {job_id}", f"Job {job_id}", f"job_id={job_id}", f"jid={job_id}", f"[{job_id}]"]
    chunks=[]
    for f in files:
        if f.exists():
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                # simple filter for lines mentioning this job id
                filtered = [ln for ln in txt if any(pat.lower() in ln.lower() for pat in patterns)]
                if filtered:
                    chunks.append(f"===== {f.name} =====\n" + "\n".join(filtered[-lines:]))
            except Exception:
                continue
    return PlainTextResponse("\n\n".join(chunks) if chunks else "(no job-specific logs)")

@app.get("/config/cycles")
def config_cycles(_: bool = Depends(verify_token)):
    return load_config().get("cycles", {})

@app.get("/config/schedules")
def config_schedules(_: bool = Depends(verify_token)):
    return load_config().get("schedules", {})

@app.get("/config")
def get_config(_: bool = Depends(verify_token)):
    return load_config()

@app.post("/config")
def set_config(data: Dict[str, Any] = Body(...), _: bool = Depends(verify_token)):
    save_config(data); return {"ok": True}
