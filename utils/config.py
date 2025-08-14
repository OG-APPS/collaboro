from __future__ import annotations
import yaml, pathlib
from typing import Mapping

DEFAULTS = {
  "ui": {"theme":"dark","accent":"#7aa2f7","auto_open_scrcpy":False,"show_toasts":True,"keyboard_shortcuts":True},
  "safety": {"warmup_seconds":120,"like_probability":0.07,"watch_lo":6,"watch_hi":13},
  "features": {"share_tap": False, "bookmark_random": False, "volume_random": False,
                "share_prob": 0.05, "bookmark_prob": 0.05, "volume_prob": 0.10},
  "system": {"api_port":8000,"artifacts":"artifacts","default_device":""},
  "video": {"presets":{"repurpose_9x16":True,"normalize_audio":False,"watermark":""}},
  "cycles": {},
  "schedules": {},
}

def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = _deep_merge(out[k], v)  # type: ignore
        else:
            out[k] = v
    return out

def load_config(path: str = "config/config.yaml"):
    p=pathlib.Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p,"w",encoding="utf-8") as f: yaml.safe_dump(DEFAULTS,f,sort_keys=False)
    with open(p,"r",encoding="utf-8") as f: data=yaml.safe_load(f) or {}
    return _deep_merge(DEFAULTS, data)

def save_config(data: dict, path: str = "config/config.yaml") -> None:
    p=pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p,"w",encoding="utf-8") as f: yaml.safe_dump(data,f,sort_keys=False)
