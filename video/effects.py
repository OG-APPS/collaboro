from __future__ import annotations
import subprocess, pathlib, shutil
from typing import Optional

def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def crop_to_9x16(in_path: str, out_path: Optional[str] = None) -> str:
    in_p = pathlib.Path(in_path)
    if not out_path: out_path = str(in_p.with_name(in_p.stem + "_9x16.mp4"))
    vf = "scale=-2:1920,crop=1080:1920"
    cmd = ["ffmpeg","-y","-i",in_path,"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","20","-an",out_path]
    subprocess.run(cmd, check=True); return out_path
