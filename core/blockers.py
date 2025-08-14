from __future__ import annotations
import time, re, pathlib, datetime, os
from typing import List, Dict, Any, Optional
from loguru import logger

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # optional; default rules still work


DEFAULT_BLOCKER_RULES: List[Dict[str, Any]] = [
    {
        "key": "tos_got_it",
        "match": {"any_text_regex": [r"(?i)changes to our terms", r"(?i)terms of service"]},
        "action": {"click_text_regex": [r"(?i)got it", r"(?i)ok", r"(?i)understood"]},
    },
    {
        "key": "birthday_gate",
        "match": {"any_text_regex": [r"(?i)when.?s your birthday", r"(?i)date of birth"]},
        "action": {"fill_birthday": "1997-01-01", "click_text_regex": [r"(?i)continue", r"(?i)next"]},
    },
    {
        "key": "deny_location",
        "match": {"any_text_regex": [r"(?i)allow .* location", r"(?i)use your location"]},
        "action": {"click_text_regex": [r"(?i)don.?t allow", r"(?i)deny"]},
    },
    {
        "key": "ads_preferences",
        "match": {"any_text_regex": [r"(?i)generic ads.*less relevant", r"(?i)personalized ads.*activity", r"(?i)relevant ads.*tiktok free"]},
        "action": {"click_text_regex": [r"(?i)generic ads", r"(?i)select"], "description": "Select generic ads for less personalized advertising"},
    },
    {
        "key": "contact_sync_deny",
        "match": {"any_text_regex": [r"(?i)tiktok is more fun with friends", r"(?i)syncing your phone contacts", r"(?i)find and get discovered by people you know"]},
        "action": {"click_text_regex": [r"(?i)don.?t allow"], "description": "Deny contact sync permission"},
    },
]


def _load_rules_from_file() -> List[Dict[str, Any]]:
    cfg_path = pathlib.Path("config/selectors.yaml")
    if not cfg_path.exists() or yaml is None:
        return DEFAULT_BLOCKER_RULES
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        br = data.get("blockers", [])
        if isinstance(br, list) and br:
            return br
    except Exception as e:
        logger.warning(f"selectors.yaml load failed: {e}")
    return DEFAULT_BLOCKER_RULES


class BlockerResolver:
    def __init__(self, serial: str, d):
        self.serial = serial
        self.d = d
        self.rules = _load_rules_from_file()
        self.sshot_dir = pathlib.Path("artifacts/logs/blockers")
        self.sshot_dir.mkdir(parents=True, exist_ok=True)

    def _any_text_matches(self, patterns: List[str]) -> bool:
        for pat in patterns:
            try:
                if self.d(textMatches=pat).exists or self.d(descriptionMatches=pat).exists:
                    return True
            except Exception:
                pass
        return False

    def _click_any(self, patterns: List[str]) -> bool:
        for pat in patterns:
            try:
                if self.d(textMatches=pat).exists:
                    self.d(textMatches=pat).click(); return True
                if self.d(descriptionMatches=pat).exists:
                    self.d(descriptionMatches=pat).click(); return True
            except Exception:
                continue
        return False

    def _fill_birthday(self, ymd: str) -> bool:
        # Very simple: try to find edit fields and send date, otherwise paste into focused
        try:
            # Focus any EditText
            edits = self.d(className="android.widget.EditText")
            if edits.exists:
                try:
                    edits.click()
                except Exception:
                    pass
            try:
                self.d.set_fastinput_ime(True)
            except Exception:
                pass
            try:
                self.d.send_keys(ymd)
                return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    def _screenshot_unresolved(self, tag: str) -> None:
        try:
            ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
            path = self.sshot_dir / f"{ts}-{self.serial}-{tag}.png"
            self.d.screenshot(str(path))
            logger.info(f"Blocker screenshot saved: {path}")
        except Exception as e:
            logger.warning(f"screenshot failed: {e}")

    def resolve(self, budget_s: float = 1.0) -> bool:
        """Try to clear known blockers within the time budget. Returns True if any action taken."""
        deadline = time.time() + max(0.1, budget_s)
        acted = False
        while time.time() < deadline:
            progressed = False
            for rule in self.rules:
                match = rule.get("match", {}) or {}
                acts = rule.get("action", {}) or {}
                pats: List[str] = match.get("any_text_regex", []) or []
                if pats and not self._any_text_matches(pats):
                    continue
                # Action: click any
                clicked = False
                if acts.get("click_text_regex"):
                    if self._click_any(list(acts["click_text_regex"])):
                        clicked = True; progressed = True; acted = True
                        time.sleep(0.3)
                # Action: fill birthday
                if not clicked and acts.get("fill_birthday"):
                    if self._fill_birthday(str(acts["fill_birthday"])):
                        progressed = True; acted = True
                        time.sleep(0.2)
                if progressed:
                    break
            if not progressed:
                # try a gentle back as fallback
                try:
                    self.d.press("back")
                    time.sleep(0.2)
                    acted = True
                except Exception:
                    pass
                break
        if not acted:
            self._screenshot_unresolved("unresolved")
        return acted


