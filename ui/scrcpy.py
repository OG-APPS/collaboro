from __future__ import annotations
import os, shutil, pathlib, subprocess, re
from typing import Optional, Tuple

def _find_scrcpy_exe() -> Optional[str]:
    p = shutil.which("scrcpy")
    if p: return p
    cand = pathlib.Path("tools/scrcpy/scrcpy.exe")
    return str(cand) if cand.exists() else None

def _scrcpy_version(exe: str) -> Tuple[int, int]:
    try:
        out = subprocess.run([exe, "-v"], capture_output=True, text=True)
        txt = out.stdout or out.stderr
        m = re.search(r"scrcpy\s+(\d+)\.(\d+)", txt)
        if m: return int(m.group(1)), int(m.group(2))
    except Exception: pass
    return (2, 0)

class ScrcpyController:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.exe: Optional[str] = _find_scrcpy_exe()
        self.ver = (0,0)
        if self.exe: self.ver = _scrcpy_version(self.exe)

    def running(self) -> bool: return self.proc is not None and self.proc.poll() is None

    def start(self, serial: str, *, control: bool=False, turn_screen_off: bool=False, stay_awake: bool=False,
              always_on_top: bool=True, max_fps: int=30, bit_rate: str="8M", window_title: Optional[str]=None):
        if not self.exe:
            self.exe = _find_scrcpy_exe()
            if not self.exe: raise RuntimeError("scrcpy not found on PATH or tools/scrcpy/scrcpy.exe")
            self.ver = _scrcpy_version(self.exe)
        if self.running(): self.stop()
        args=[self.exe,"-s",serial,"--max-fps",str(max_fps)]
        major,_=self.ver
        if bit_rate:
            args += ["--video-bit-rate" if major>=3 else "--bit-rate", str(bit_rate)]
        if not control: args.append("--no-control")
        if turn_screen_off: args.append("-S")
        if stay_awake: args.append("--stay-awake")
        if always_on_top: args.append("--always-on-top")
        if window_title: args += ["--window-title", window_title]
        self.proc = subprocess.Popen(args, creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0))

    def stop(self):
        if not self.running():
            self.proc = None; return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except Exception:
                # Force kill if not exiting promptly
                try:
                    self.proc.kill()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self.proc = None

SCRCPY = ScrcpyController()
