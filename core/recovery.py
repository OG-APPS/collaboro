from __future__ import annotations
from loguru import logger
import time
import subprocess

class Recovery:
    def __init__(self, serial: str, d):
        self.serial=serial; self.d=d

    def escalate(self, level:int):
        if level==1:
            logger.warning("Recovery L1: force-stop app and relaunch")
            try:
                # best effort: find foreground app package if ui2 exposes it; else no-op here
                # fallback to press back/home
                self.d.press("back"); time.sleep(0.3)
            except Exception: pass
        elif level==2:
            logger.warning("Recovery L2: wait+restart app")
            time.sleep(30)
            try:
                self.d.press("home"); time.sleep(0.5)
            except Exception: pass
        elif level>=3:
            logger.error("Recovery L3: adb reboot and reconnect")
            try:
                subprocess.run(["adb","-s",self.serial,"reboot"], check=False)
            except Exception: pass
            # wait for device to come back
            time.sleep(10)
