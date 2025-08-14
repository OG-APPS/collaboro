from __future__ import annotations
import time
from typing import Optional


def is_on_feed_ui(d) -> bool:
    """Fast UI-based check for being on the main feed/home.

    Heuristics (cheap → richer):
    - Bottom nav content descriptions: Home/For You/Following/Friends/Inbox/Profile
    - Presence of Like/Comment/Share controls on the right rail
    - Top tabs: For You / Following
    """
    try:
        # Bottom nav or top tabs (localized matches best-effort)
        if d(descriptionMatches=r"(?i)home|for\s*you|following|friends|inbox|profile").exists:
            return True
        if d(textMatches=r"(?i)for\s*you|following|home").exists:
            return True
        # Right action rail
        if d(descriptionMatches=r"(?i)like").exists and d(descriptionMatches=r"(?i)comment").exists:
            return True
        if d(descriptionMatches=r"(?i)share").exists and d(descriptionMatches=r"(?i)comment").exists:
            return True
    except Exception:
        pass
    return False


def wait_on_feed(d, timeout_s: float = 6.0) -> bool:
    end = time.time() + max(0.5, timeout_s)
    while time.time() < end:
        if is_on_feed_ui(d):
            return True
        time.sleep(0.2)
    return False
