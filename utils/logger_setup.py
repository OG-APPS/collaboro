from __future__ import annotations
from loguru import logger
import sys, pathlib

def setup_logger(component: str, log_dir: str = "artifacts/logs") -> None:
    pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
    logfile = pathlib.Path(log_dir) / f"{component}.log"
    logger.remove()
    logger.add(sys.stdout, level="INFO", enqueue=True)
    logger.add(str(logfile), rotation="10 MB", retention=5, compression="zip", level="INFO", enqueue=True)
