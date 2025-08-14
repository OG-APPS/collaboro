from __future__ import annotations
import subprocess, json, shlex, os
from typing import Dict, Any, List, Optional
from loguru import logger


def run_external(command: str, args: Optional[List[str]] = None, timeout: int = 30, cwd: Optional[str] = None) -> Dict[str, Any]:
    """
    Execute an external command/script and attempt to parse JSON from stdout.
    Returns a dict with {ok, exit_code, stdout, stderr, data?} where data is parsed JSON if available.
    """
    cmd_list: List[str]
    if os.name == "nt" and command.lower().endswith(".ps1"):
        # PowerShell script on Windows
        cmd_list = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", command]
        if args:
            cmd_list += args
    else:
        # Generic command
        cmd_list = [command] + (args or [])
    logger.info(f"Running external: {cmd_list} (timeout={timeout}s, cwd={cwd or os.getcwd()})")
    try:
        proc = subprocess.run(cmd_list, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        res: Dict[str, Any] = {"ok": proc.returncode == 0, "exit_code": proc.returncode, "stdout": out, "stderr": err}
        try:
            if out:
                res["data"] = json.loads(out)
        except Exception as e:
            logger.warning(f"External output is not JSON: {e}")
        return res
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": -1, "stdout": "", "stderr": "timeout"}
    except FileNotFoundError as e:
        return {"ok": False, "exit_code": -2, "stdout": "", "stderr": str(e)}
    except Exception as e:
        return {"ok": False, "exit_code": -3, "stdout": "", "stderr": str(e)}

ext
