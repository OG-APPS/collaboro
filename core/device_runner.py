from __future__ import annotations
import time, random, subprocess, os
from typing import Dict, Any, List, Optional, Callable
from loguru import logger
from interfaces.ui2 import connect
from core.state_machine import StateMachine
from core.blockers import BlockerResolver
from core.permissions import Permissions
from core.fsm import FiniteStateMachine, AppState
from utils.user_logger import user_logger

TIKTOK_PACKAGES = [
    "com.zhiliaoapp.musically",
    "com.ss.android.ugc.trill",
    "com.ss.android.ugc.aweme",
]

def _adb(*args: str) -> int:
    return subprocess.call(list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

class DeviceRunner:
    def __init__(self, serial: str):
        self.serial = serial
        logger.info(f"Connecting to {serial}")
        self.d = connect(serial)
        self.sm = StateMachine(serial, self.d)
        self.pkg: Optional[str] = None
        self.blockers = BlockerResolver(serial, self.d)
        self.perm = Permissions(self.d)
        self.fsm = FiniteStateMachine(serial, self.d)

    def wake_and_unlock(self):
        try:
            if not self.d.screen_on():
                self.d.screen_on(); time.sleep(0.8)
            self.d.unlock()
        except Exception:
            try:
                subprocess.run(["adb","-s",self.serial,"shell","input","keyevent","224"])  # wake
                time.sleep(0.4)
                # Fallback swipe up gesture (may vary by device)
                subprocess.run(["adb","-s",self.serial,"shell","swipe","400","1200","400","300","200"])  # swipe up
            except Exception as e:
                logger.warning(f"Unlock fallback failed: {e}")
        # Post-condition check: ensure screen is on and device responds to a simple UI query
        try:
            ok = bool(self.d.info)
            if not ok:
                logger.warning("Device did not respond after unlock attempts")
        except Exception as e:
            logger.warning(f"Device info unavailable after unlock: {e}")

    def _resolve_pkg(self) -> str:
        for p in TIKTOK_PACKAGES:
            try:
                if self.d.app_info(p):
                    return p
            except Exception:
                pass
        return TIKTOK_PACKAGES[0]

    def start_tiktok(self):
        if not self.pkg:
            self.pkg = self._resolve_pkg()
        if not self.pkg:
            logger.error("No TikTok package found on device; aborting launch")
            return
        logger.info(f"Starting TikTok package {self.pkg}")
        user_logger.tiktok_launched(self.serial, self.pkg)

        try:
            self.d.app_start(self.pkg, use_monkey=True)
        except Exception as e:
            logger.warning(f"app_start failed ({e}); trying adb monkey")
            _adb("adb","-s",self.serial,"shell","monkey","-p", self.pkg, "1")
        time.sleep(2.0)
        # Verify app is responsive by querying app_info
        try:
            _ = self.d.app_info(self.pkg)
        except Exception as e:
            logger.warning(f"App info unavailable after launch: {e}")

        # Check initial page after launch
        try:
            from interfaces.screen_monitor import ScreenMonitor
            monitor = ScreenMonitor(self.d, self.serial)
            monitor.print_screen_state(force=True)
        except Exception:
            pass

        # resolve blockers after launch
        try:
            self.blockers.resolve(1.0)
            user_logger.popup_dismissed(self.serial, "App blockers")
        except Exception:
            pass
        try:
            self.perm.dismiss_popups(1.0)
            user_logger.popup_dismissed(self.serial, "Permissions")
        except Exception:
            pass

    def warmup(self, seconds: int = 60, like_prob: float = 0.07, should_continue: Optional[Callable[[], bool]] = None) -> bool:
        self.wake_and_unlock(); self.start_tiktok()
        # steer to FYP via FSM before starting
        try:
            self.fsm.run_until({AppState.FYP_READY}, budget_s=5.0)
        except Exception:
            pass
        t0=time.time()
        # periodic blocker resolve
        def _hook():
            try: self.blockers.resolve(0.5)
            except Exception: pass
        ok = self.sm.warmup(seconds=seconds, like_prob=like_prob, should_continue=should_continue)
        _hook()
        return ok

    def post_video(self, video_path: str, caption: str = "", should_continue: Optional[Callable[[], bool]] = None) -> bool:
        logger.info(f"Posting video: {video_path} (caption len={len(caption)})")
        if not os.path.exists(video_path):
            logger.error(f"Video not found: {video_path}")
            return False
        # Best-effort flow: push, open upload, pick, caption, post
        dst = "/sdcard/Movies/ta_upload.mp4"
        try:
            subprocess.run(["adb","-s",self.serial,"push", video_path, dst], check=False)
        except Exception as e:
            logger.error(f"adb push failed: {e}")
            return False

        self.wake_and_unlock(); self.start_tiktok()
        try: self.blockers.resolve(1.0)
        except Exception: pass
        if should_continue and not should_continue():
            logger.info("Cancelled before upload flow")
            return False

        # Tap '+' area
        try:
            self.d.click(0.50, 0.92); time.sleep(1.6)
        except Exception: pass
        try: self.blockers.resolve(0.8)
        except Exception: pass

        # Try 'Upload' or similar
        for txt in ("Upload","Post","Upload video","Next"):
            try:
                if self.d(text=txt).exists:
                    self.d(text=txt).click(); time.sleep(1.2); break
            except Exception: pass

        if should_continue and not should_continue(): return False

        # Pick first grid item
        try: self.d.click(0.15, 0.25); time.sleep(0.8)
        except Exception: pass

        for txt in ("Next","Done","Confirm"):
            try:
                if self.d(text=txt).exists:
                    self.d(text=txt).click(); time.sleep(1.0); break
            except Exception: pass
        try: self.blockers.resolve(0.8)
        except Exception: pass

        if should_continue and not should_continue(): return False

        # Caption field attempts
        try:
            el = None
            if self.d(descriptionContains="Add caption").exists:
                el = self.d(descriptionContains="Add caption")
            elif self.d(textContains="Add caption").exists:
                el = self.d(textContains="Add caption")
            if el:
                el.click(); time.sleep(0.6)
                try:
                    self.d.set_fastinput_ime(True)
                except Exception:
                    pass
                try:
                    self.d.send_keys(caption)
                except Exception:
                    pass
        except Exception:
            try:
                self.d.click(0.5, 0.3); time.sleep(0.4)
                self.d.send_keys(caption)
            except Exception: pass

        # Post / Publish
        for txt in ("Post","Publish","Share"):
            try:
                if self.d(text=txt).exists:
                    self.d(text=txt).click(); time.sleep(2.0); break
            except Exception: pass
        try:
            self.d.click(0.90, 0.93)
        except Exception: pass
        time.sleep(2.0)
        return True

    def run_pipeline(self, payload: Dict[str, Any], should_continue: Optional[Callable[[], bool]] = None) -> bool:
        steps: List[Dict[str, Any]] = payload.get("steps", [])
        repeat = int(payload.get("repeat", 1))
        sb = payload.get("sleep_between", [2, 5]) or [2, 5]
        try:
            lo = float(sb[0])
            hi = float(sb[1]) if len(sb) > 1 else lo
            if hi < lo:
                lo, hi = hi, lo
        except Exception:
            lo, hi = 2.0, 5.0
        ok=True
        for _ in range(repeat):
            for st in steps:
                if should_continue and not should_continue():
                    logger.info("Pipeline interrupted")
                    return False
                t = st.get("type")
                if t=="warmup":
                    d = st.get("duration")
                    dur = int(d) if d is not None else 60
                    lp = st.get("like_prob")
                    likep = float(lp) if lp is not None else 0.07
                    ok = self.warmup(dur, likep, should_continue) and ok
                elif t=="break":
                    d = st.get("duration")
                elif t=="ip_rotate" or t=="verify_profile":
                    # Execute external command per step configuration via API adapter
                    try:
                        from orchestrator.external_adapter import run_external
                    except Exception:
                        logger.error("External adapter not available"); ok = False; continue
                    cmd = st.get("command"); args = st.get("args") or []
                    timeout = int(st.get("timeout", 30)); cwd = st.get("working_dir")
                    if not cmd:
                        logger.warning(f"{t} step missing command"); continue
                    res = run_external(cmd, args=args, timeout=timeout, cwd=cwd)
                    # Store result in runs table notes via API (sessions audit trail could be added)
                    try:
                        from orchestrator.api import get_db
                        conn = get_db()
                        conn.execute("UPDATE runs SET status=? WHERE job_id=(SELECT id FROM jobs ORDER BY id DESC LIMIT 1)",
                                     ("running",))
                        # For demo: append to runs table via a temp table or write to logs; keeping simple here
                        logger.info(f"External step {t} -> ok={res.get('ok')} code={res.get('exit_code')}")
                        conn.close()
                    except Exception:
                        pass
                    if not res.get("ok"):
                        ok = False

                    dur = int(d) if d is not None else 60
                    for _i in range(dur):
                        if should_continue and not should_continue():
                            return False
                        time.sleep(1)
                elif t=="post_video":
                    vp = st.get("video",""); cap = st.get("caption","")
                    ok = self.post_video(vp, cap, should_continue) and ok
                elif t=="rotate_identity":
                    logger.info("Rotate identity (soft): clear + restart app"); time.sleep(2.0)
                else:
                    logger.warning(f"Unknown step: {t}")
                time.sleep(random.uniform(lo,hi))
        return ok
