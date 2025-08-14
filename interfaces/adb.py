from __future__ import annotations
import subprocess, shlex

def shell(serial: str, cmd: str) -> int:
    args = ["adb","-s",serial,"shell"] + shlex.split(cmd)
    return subprocess.call(args)

def input_key(serial: str, key: int) -> int:
    return shell(serial, f"input keyevent {key}")

def swipe(serial: str, x1:int,y1:int,x2:int,y2:int,duration_ms:int=200) -> int:
    return shell(serial, f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
