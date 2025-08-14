"""
User-friendly logging system for TikTok automation.

Inter-process safe implementation backed by SQLite. All processes (API, worker, UI)
append to a shared table in artifacts/orchestrator.db, avoiding JSON file races.
"""

from __future__ import annotations
import threading
import sqlite3
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

DB_PATH = os.environ.get("DB_PATH", "artifacts/orchestrator.db")

@dataclass
class UserLogEntry:
    timestamp: datetime
    device: str
    action: str
    details: str = ""
    status: str = "info"  # info, success, warning, error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.strftime("%H:%M:%S"),
            "device": self.device,
            "action": self.action,
            "details": self.details,
            "status": self.status,
        }

class UserLogger:
    """Inter-process safe user-friendly logger using SQLite."""

    def __init__(self, max_entries: int = 1000):
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        return conn

    def _ensure_db(self) -> None:
        try:
            conn = self._connect()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_logs(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                  device TEXT,
                  action TEXT,
                  details TEXT,
                  status TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_user_logs_ts_id ON user_logs(ts, id);
                CREATE INDEX IF NOT EXISTS idx_user_logs_device_ts ON user_logs(device, ts);
                """
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"UserLogger: DB init failed: {e}")

    def _rowcount(self, conn: sqlite3.Connection) -> int:
        try:
            r = conn.execute("SELECT COUNT(1) FROM user_logs").fetchone()
            return int(r[0]) if r else 0
        except Exception:
            return 0

    def _add_entry(self, device: str, action: str, details: str = "", status: str = "info") -> None:
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "INSERT INTO user_logs(device, action, details, status) VALUES(?,?,?,?)",
                    (device, action, details, status),
                )
                # Trim only when exceeding threshold (max_entries + 200 buffer)
                try:
                    total = self._rowcount(conn)
                    if total > (self.max_entries + 200):
                        conn.execute(
                            "DELETE FROM user_logs WHERE id NOT IN (SELECT id FROM user_logs ORDER BY id DESC LIMIT ?)",
                            (self.max_entries,),
                        )
                except Exception:
                    pass
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"UserLogger: write failed: {e}")
        # Also log to technical logger for debugging
        try:
            logger.info(f"[{device}] {action}" + (f" - {details}" if details else ""))
        except Exception:
            pass

    def get_recent_logs(self, device: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            conn = self._connect()
            if device:
                rows = conn.execute(
                    "SELECT ts, device, action, details, status FROM user_logs WHERE device=? ORDER BY id DESC LIMIT ?",
                    (device, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ts, device, action, details, status FROM user_logs ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            conn.close()
            out: List[Dict[str, Any]] = []
            for r in rows:
                try:
                    ts = r["ts"]
                    if isinstance(ts, str):
                        # SQLite CURRENT_TIMESTAMP returns 'YYYY-MM-DD HH:MM:SS'
                        dt = datetime.fromisoformat(ts.replace(" ", "T"))
                    else:
                        dt = datetime.now()
                except Exception:
                    dt = datetime.now()
                out.append(
                    UserLogEntry(
                        timestamp=dt,
                        device=r["device"],
                        action=r["action"],
                        details=r["details"] or "",
                        status=r["status"] or "info",
                    ).to_dict()
                )
            return out
        except Exception as e:
            logger.warning(f"UserLogger: read failed: {e}")
            return []

    def clear_logs(self, device: Optional[str] = None) -> None:
        try:
            conn = self._connect()
            if device:
                conn.execute("DELETE FROM user_logs WHERE device=?", (device,))
            else:
                conn.execute("DELETE FROM user_logs")
            conn.commit(); conn.close()
            logger.info(f"UserLogger: Cleared logs for device={device or 'all'}")
        except Exception as e:
            logger.warning(f"UserLogger: clear failed: {e}")

    # User-friendly action methods
    def tiktok_launched(self, device: str, package: str):
        self._add_entry(device, "🚀 TikTok Launched", f"Package: {package}", "success")

    def feed_detected(self, device: str):
        self._add_entry(device, "📱 TikTok Feed Detected", "Ready for automation", "success")

    def warmup_started(self, device: str, duration: int, like_prob: float):
        self._add_entry(device, "🔥 Warmup Started", f"{duration}s, Like probability: {like_prob:.0%}", "info")

    def scrolling(self, device: str, video_count: Optional[int] = None):
        details = f"Video #{video_count}" if video_count else "Scrolling to next video"
        self._add_entry(device, "📜 Scrolling", details, "info")

    def watching_video(self, device: str, duration: float, video_count: Optional[int] = None):
        video_info = f"Video #{video_count} - " if video_count else ""
        self._add_entry(device, "👀 Watching Video", f"{video_info}Duration: {duration:.1f}s", "info")

    def liked_video(self, device: str, video_count: Optional[int] = None):
        details = f"Video #{video_count}" if video_count else "Liked current video"
        self._add_entry(device, "❤️ Liked Video", details, "success")

    def swipe_coordinates(self, device: str, from_x: int, from_y: int, to_x: int, to_y: int):
        self._add_entry(device, "👆 Swiping", f"From ({from_x},{from_y}) to ({to_x},{to_y})", "info")

    def scroll_successful(self, device: str):
        self._add_entry(device, "✅ Scroll Successful", "Feed detected after swipe", "success")

    def popup_detected(self, device: str, popup_type: str):
        self._add_entry(device, "🔔 Popup Detected", f"Type: {popup_type}", "warning")

    def popup_dismissed(self, device: str, popup_type: str):
        self._add_entry(device, "✅ Popup Dismissed", f"Handled: {popup_type}", "success")

    def warmup_completed(self, device: str, duration: float, videos_watched: Optional[int] = None, likes_given: Optional[int] = None):
        stats: List[str] = []
        if videos_watched: stats.append(f"{videos_watched} videos")
        if likes_given: stats.append(f"{likes_given} likes")
        details = f"Duration: {duration:.1f}s" + (f", {', '.join(stats)}" if stats else "")
        self._add_entry(device, "🎉 Warmup Completed", details, "success")

    def warmup_interrupted(self, device: str, reason: str = "User cancelled"):
        self._add_entry(device, "⏹️ Warmup Interrupted", reason, "warning")

    def error_occurred(self, device: str, action: str, error: str):
        self._add_entry(device, f"❌ Error in {action}", error, "error")

    def recovery_attempt(self, device: str, issue: str):
        self._add_entry(device, "🔧 Recovery Attempt", f"Issue: {issue}", "warning")

    def recovery_successful(self, device: str, action: str):
        self._add_entry(device, "✅ Recovery Successful", action, "success")

    def device_connected(self, device: str, model: Optional[str] = None):
        details = f"Model: {model}" if model else "Device ready"
        self._add_entry(device, "🔌 Device Connected", details, "success")

    def device_disconnected(self, device: str):
        self._add_entry(device, "🔌 Device Disconnected", "", "error")

    def page_detected(self, device: str, page_type: str, details: str = ""):
        page_names = {
            "FEED": "📱 TikTok Feed",
            "LOGIN": "🔐 TikTok Login",
            "AGE_VERIFICATION": "🎂 Age Verification",
            "NOTIFICATION_PERMISSION": "🔔 Notification Permission",
            "LOCATION_PERMISSION": "📍 Location Permission",
            "CONTACT_SYNC_PERMISSION": "👥 Contact Sync Permission",
            "TERMS_OF_SERVICE": "📋 Terms of Service",
            "PRIVACY_POLICY": "🔒 Privacy Policy",
            "ADS_PREFERENCES": "📺 Ads Preferences",
            "GENERIC_DIALOG": "💬 Dialog Box",
            "APP_UPDATE": "⬆️ App Update",
            "NETWORK_ERROR": "🌐 Network Error",
            "ERROR_DIALOG": "❌ Error Dialog",
            "HOME_SCREEN": "🏠 Android Home",
            "ANDROID_SETTINGS": "⚙️ Android Settings",
            "PROFILE_PAGE": "👤 TikTok Profile",
            "DISCOVER_PAGE": "🔍 TikTok Discover",
            "UNKNOWN": "❓ Unknown Page",
        }
        page_name = page_names.get(page_type, f"📄 {page_type.replace('_', ' ').title()}")
        self._add_entry(device, f"Page Detected: {page_name}", details, "info")

    def page_transition(self, device: str, from_page: str, to_page: str):
        self._add_entry(device, "🔄 Page Transition", f"{from_page} → {to_page}", "info")

    def automation_stuck(self, device: str, page_type: str, duration: float):
        self._add_entry(device, "⏸️ Automation Stuck", f"On {page_type} for {duration:.1f}s", "warning")

    def suggestion_provided(self, device: str, page_type: str, suggestions: List[str]):
        suggestion_text = "; ".join(suggestions[:2])
        if len(suggestions) > 2:
            suggestion_text += f" (+{len(suggestions)-2} more)"
        self._add_entry(device, "💡 Suggestions", f"{page_type}: {suggestion_text}", "info")

# Global singleton instance
_user_logger_instance = None

def get_user_logger():
    """Get the global user logger instance (singleton)."""
    global _user_logger_instance
    if _user_logger_instance is None:
        _user_logger_instance = UserLogger()
    return _user_logger_instance

# For backward compatibility
user_logger = get_user_logger()
