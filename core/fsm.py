from __future__ import annotations
import time, re, pathlib
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set
from loguru import logger

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from core.blockers import BlockerResolver


class AppState(Enum):
    UNKNOWN = auto()
    BIRTHDAY_GATE = auto()
    LOGIN_SHEET = auto()
    GOOGLE_ACCOUNT_PICKER = auto()
    NICKNAME = auto()
    SWIPE_TUTORIAL = auto()
    FYP_READY = auto()
    BLOCKER_MODAL = auto()


def _read_states_yaml() -> Dict[str, Any]:
    cfg = pathlib.Path("config/states.yaml")
    if yaml and cfg.exists():
        try:
            return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"states.yaml parse failed: {e}")
    # default minimal graph
    return {
        "states": {
            "BIRTHDAY_GATE": {
                "detect": {"text_any": [r"(?i)when.?s your birthday", r"(?i)date of birth", r"(?i)birthday"]},
                "actions": {
                    "login_existing": {"click_text_any": [r"(?i)log in", r"(?i)sign in"]},
                    "create_new": {"fill_birthday": "1997-01-01", "then_click": [r"(?i)continue", r"(?i)next"]},
                },
                "exit": {"text_any": [r"(?i)log in to tiktok", r"(?i)home|for you"]},
                "timeout_s": 12,
            },
            "LOGIN_SHEET": {
                "detect": {"text_any": [r"(?i)log in to tiktok", r"(?i)use phone / email / username", r"(?i)continue with google"]},
                "actions": {
                    "google": {"click_text_any": [r"(?i)continue with google", r"(?i)google"]},
                    "password": {"click_text_any": [r"(?i)use phone / email / username"]},
                },
                "exit": {"text_any": [r"(?i)choose an account", r"(?i)password"]},
                "timeout_s": 10,
            },
            "FYP_READY": {
                "detect": {"text_any": [r"(?i)for you|home"]},
                "exit": {"text_any": [r"(?i)for you|home"]},
                "timeout_s": 2,
            },
        }
    }


class FiniteStateMachine:
    def __init__(self, serial: str, d, *, auth_strategy: str = "login_existing", auth_method: str = "google"):
        self.serial = serial
        self.d = d
        self.blockers = BlockerResolver(serial, d)
        self.graph = _read_states_yaml().get("states", {})
        self.auth_strategy = auth_strategy
        self.auth_method = auth_method

    def _any_text_matches(self, patterns: List[str]) -> bool:
        for pat in patterns or []:
            try:
                if self.d(textMatches=pat).exists or self.d(descriptionMatches=pat).exists:
                    return True
            except Exception:
                continue
        return False

    def detect(self) -> AppState:
        # quick blocker modal pass
        try:
            self.blockers.resolve(0.2)
        except Exception:
            pass
        for name, node in self.graph.items():
            det = node.get("detect", {})
            if self._any_text_matches(det.get("text_any", []) or []):
                try:
                    return AppState[name]
                except Exception:
                    return AppState.UNKNOWN
        # heuristic feed
        try:
            if self._any_text_matches([r"(?i)for you|home"]):
                return AppState.FYP_READY
        except Exception:
            pass
        return AppState.UNKNOWN

    def _click_any(self, patterns: List[str]) -> bool:
        for pat in patterns or []:
            try:
                if self.d(textMatches=pat).exists:
                    self.d(textMatches=pat).click(); return True
                if self.d(descriptionMatches=pat).exists:
                    self.d(descriptionMatches=pat).click(); return True
            except Exception:
                continue
        return False

    def _fill_birthday(self, ymd: str) -> bool:
        try:
            self.d.set_fastinput_ime(True)
        except Exception:
            pass
        try:
            edits = self.d(className="android.widget.EditText")
            if edits.exists:
                edits.click(); time.sleep(0.2)
            self.d.send_keys(ymd)
            return True
        except Exception:
            return False

    def act(self, state: AppState) -> None:
        node = self.graph.get(state.name, {})
        acts = node.get("actions", {})
        # Choose branch by configured strategy/method
        branch = None
        if state is AppState.BIRTHDAY_GATE:
            branch = acts.get("login_existing" if self.auth_strategy=="login_existing" else "create_new")
        elif state is AppState.LOGIN_SHEET:
            branch = acts.get("google" if self.auth_method=="google" else "password")
        if not branch:
            return
        # Execute branch primitives
        if branch.get("click_text_any"):
            self._click_any(list(branch["click_text_any"]))
            time.sleep(0.4)
        if branch.get("fill_birthday"):
            if self._fill_birthday(str(branch["fill_birthday"])):
                time.sleep(0.2)
        if branch.get("then_click"):
            self._click_any(list(branch["then_click"]))
            time.sleep(0.4)

    def exit_met(self, state: AppState) -> bool:
        node = self.graph.get(state.name, {})
        ex = node.get("exit", {})
        if self._any_text_matches(ex.get("text_any", []) or []):
            return True
        # FYP is both detect and exit
        if state is AppState.FYP_READY:
            return True
        return False

    def run_until(self, targets: Set[AppState], budget_s: float = 8.0) -> bool:
        """Drive the app until one of the target states is detected or budget exhausted."""
        end = time.time() + max(0.5, budget_s)
        while time.time() < end:
            st = self.detect()
            if st in targets:
                return True
            if st is AppState.UNKNOWN:
                # gentle back to clear overlays
                try: self.d.press("back"); time.sleep(0.2)
                except Exception: pass
                continue
            # perform one action and re-evaluate
            try:
                self.act(st)
            except Exception:
                pass
            # quick re-check exit
            try:
                if self.exit_met(st):
                    # loop again to detect next state
                    time.sleep(0.2)
            except Exception:
                pass
        logger.info("FSM budget exhausted before reaching targets")
        return False


