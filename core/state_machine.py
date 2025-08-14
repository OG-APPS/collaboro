from __future__ import annotations
from loguru import logger
import time
from typing import Callable, Optional
from enum import Enum, auto
from interfaces.vision import is_on_feed_ui, wait_on_feed
from core.actions import Actions
from core.recovery import Recovery
from core.permissions import Permissions
from utils.user_logger import user_logger
from interfaces.screen_monitor import ScreenMonitor

class StateMachine:
    def __init__(self, serial: str, d):
        self.serial=serial; self.d=d
        self.act=Actions(d); self.rec=Recovery(serial,d); self.perm=Permissions(d)
        self.monitor = ScreenMonitor(d, serial)
        # Ensure Actions has access to the serial
        self.act.serial = serial

    class Page(Enum):
        UNKNOWN = auto()
        FEED = auto()
        LOGIN = auto()
        DIALOG = auto()

    def detect_page(self) -> "StateMachine.Page":
        try:
            if is_on_feed_ui(self.d):
                return self.Page.FEED
            # Heuristics for login and dialogs
            if self.d(textMatches=r"(?i)log in|sign in").exists:
                return self.Page.LOGIN
            if self.d(textMatches=r"(?i)when.?s your birthday|terms of service|got it|ok|continue").exists:
                return self.Page.DIALOG
        except Exception:
            pass
        return self.Page.UNKNOWN

    def ensure_ready_for_warmup(self, settle_s: float = 1.0) -> bool:
        """Bring the app to a state suitable for warmup (on feed)."""
        p = self.detect_page()
        if p == self.Page.FEED:
            return True
        # Try easy fixes
        try:
            self.perm.dismiss_popups(1.0)
        except Exception:
            pass
        try:
            # In case a dialog blocks, a back press may help
            self.d.press("back")
        except Exception:
            pass
        ok = wait_on_feed(self.d, timeout_s=3.0)
        if ok:
            time.sleep(max(0.0, settle_s))
        else:
            logger.info("Not on feed after attempts; proceed cautiously")
        return ok

    def warmup(self, seconds:int=60, like_prob:float=0.05, should_continue: Optional[Callable[[], bool]] = None):
        logger.info(f"Warmup start {seconds}s, like_prob={like_prob}")
        user_logger.warmup_started(self.serial, seconds, like_prob)

        # Print initial screen state
        print(f"\n🔍 Starting warmup - checking initial screen state...")
        self.monitor.print_screen_state(force=True)

        # Try to ensure feed context
        try:
            self.ensure_ready_for_warmup(0.5)
            user_logger.feed_detected(self.serial)
        except Exception:
            pass

        t0=time.time()
        video_count = 0
        likes_given = 0

        while time.time()-t0 < seconds:
            if should_continue and not should_continue():
                logger.info("Warmup interrupted by cancel/pause")
                user_logger.warmup_interrupted(self.serial, "User cancelled")
                return False

            # Monitor screen state every few iterations
            if video_count % 3 == 0:  # Check every 3rd video
                if self.monitor.monitor_and_suggest():
                    # If stuck, try to recover
                    print(f"🔧 Attempting recovery...")
                    try:
                        self.rec.recover_to_feed()
                        user_logger.recovery_attempt(self.serial, "Stuck state recovery")
                    except Exception as e:
                        logger.error(f"Recovery failed: {e}")
                        user_logger.error_occurred(self.serial, "Recovery", str(e))

            video_count += 1

            # Watch video for a realistic duration (use config range if available)
            try:
                from utils.config import load_config
                cfg = load_config()
                lo = float(cfg.get("safety",{}).get("watch_lo", 6))
                hi = float(cfg.get("safety",{}).get("watch_hi", 13))
                if hi < lo: lo, hi = hi, lo
            except Exception:
                lo, hi = 6.0, 13.0
            import random
            watch_duration = random.uniform(lo, hi)
            user_logger.watching_video(self.serial, watch_duration, video_count)
            time.sleep(watch_duration)

            # Try to like (use configured like probability default if call-site passes None)
            try:
                from utils.config import load_config
                if like_prob is None:
                    like_prob = float(load_config().get("safety",{}).get("like_probability", 0.07))
            except Exception:
                pass
            if self.act.like(like_prob):
                likes_given += 1
                user_logger.liked_video(self.serial, video_count)

            # Optional human-like micro-actions during watch
            try:
                from utils.config import load_config
                cfg = load_config(); feats = cfg.get("features", {})
                import random
                # Share tap
                if feats.get("share_tap", False) and random.random() < float(feats.get("share_prob", 0.05)):
                    if self.act.tap_share_then_back():
                        user_logger.share_tapped(self.serial)
                        time.sleep(0.3 + random.random()*0.4)
                # Bookmark
                if feats.get("bookmark_random", False) and random.random() < float(feats.get("bookmark_prob", 0.05)):
                    if self.act.toggle_bookmark():
                        user_logger.bookmarked(self.serial)
                        time.sleep(0.2 + random.random()*0.3)
                # Volume nudge
                if feats.get("volume_random", False) and random.random() < float(feats.get("volume_prob", 0.1)):
                    dirn = self.act.random_volume_nudge()
                    if dirn != "error":
                        user_logger.volume_adjusted(self.serial, dirn)
                        time.sleep(0.2)
            except Exception:
                pass

            # Swipe to next video
            user_logger.scrolling(self.serial, video_count + 1)
            self.act.swipe_up()

            # Check if swipe was successful by monitoring screen
            time.sleep(1.0)  # Wait for screen to update
            # Only log scroll success if we appear to be on feed; otherwise, log a softer info
            try:
                on_feed = is_on_feed_ui(self.d)
            except Exception:
                on_feed = False
            self.monitor.print_screen_state()  # Print new state (less verbose if unchanged)
            if on_feed:
                user_logger.scroll_successful(self.serial)
            else:
                logger.info("Swipe completed, but feed not confirmed; continuing")

            # Brief pause between actions
            time.sleep(0.5 + (time.time() % 1.0))  # 0.5-1.5 seconds

        elapsed = time.time() - t0
        logger.info("Warmup done")
        user_logger.warmup_completed(self.serial, elapsed, video_count, likes_given)
        return True
