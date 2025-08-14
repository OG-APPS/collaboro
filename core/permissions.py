from __future__ import annotations
from typing import Optional
from loguru import logger
import time

class Permissions:
    """Handles common runtime permission and first-run dialogs.

    Best-effort: uses text/description regex matches and safe fallbacks.
    """
    def __init__(self, d):
        self.d = d

    def _click_any(self, *patterns: str) -> bool:
        for pat in patterns:
            try:
                if self.d(textMatches=pat).exists:
                    self.d(textMatches=pat).click(); return True
                if self.d(descriptionMatches=pat).exists:
                    self.d(descriptionMatches=pat).click(); return True
            except Exception:
                continue
        return False

    def deny_location(self) -> bool:
        return self._click_any(r"(?i)don.?t allow", r"(?i)deny")

    def allow_notifications(self) -> bool:
        return self._click_any(r"(?i)allow", r"(?i)turn on")

    def allow_storage(self) -> bool:
        return self._click_any(r"(?i)allow", r"(?i)ok")

    def generic_dismiss(self) -> bool:
        return self._click_any(r"(?i)got it", r"(?i)ok", r"(?i)continue", r"(?i)close", r"(?i)understood")

    def dismiss_popups(self, budget_s: float = 1.0) -> bool:
        """Try to clear common permission dialogs within a time budget.

        Returns True if any action taken.
        """
        deadline = time.time() + max(0.2, budget_s)
        acted = False
        while time.time() < deadline:
            progressed = False
            # Location prompts
            if self._click_any(r"(?i)while using the app", r"(?i)only this time", r"(?i)don.?t allow", r"(?i)deny"):
                progressed = True
            # Notifications prompts
            elif self._click_any(r"(?i)allow notifications", r"(?i)turn on", r"(?i)not now"):
                progressed = True
            # Storage/media prompts
            elif self._click_any(r"(?i)allow access", r"(?i)allow"):
                progressed = True
            # Generic banners / info OK
            elif self.generic_dismiss():
                progressed = True
            if progressed:
                acted = True
                time.sleep(0.2)
            else:
                break
        if acted:
            logger.info("Permissions: popups handled")
        return acted
