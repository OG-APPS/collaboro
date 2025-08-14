from __future__ import annotations
import uiautomator2 as u2
from loguru import logger

def connect(serial: str):
    logger.info(f"UI2 connecting: {serial}")
    d = u2.connect(serial)
    try:
        d.healthcheck()
    except Exception:
        pass
    try:
        # Enable fast input IME so send_keys works reliably
        d.set_fastinput_ime(True)
    except Exception:
        pass
    try:
        # Simple watchers for common dialogs
        d.watcher("ok_got_it").when(textMatches=r"(?i)got it|ok|understood").click(textMatches=r"(?i)got it|ok|understood")
        d.watcher("deny_location").when(textMatches=r"(?i)allow .* location|use your location").click(textMatches=r"(?i)don.?t allow|deny")
        d.watcher.start()
    except Exception:
        pass
    return d
