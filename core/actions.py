from __future__ import annotations
import time, random
from loguru import logger
from utils.user_logger import user_logger

class Actions:
    def __init__(self, d):
        self.d = d
        # Try to get serial from device object or use unknown
        self.serial = getattr(d, 'serial', None) or getattr(d, '_serial', 'unknown')

    def swipe_up(self):
        """Swipe up to next video with randomized coordinates."""
        try:
            # Add some randomization to make it more human-like
            start_x = 0.45 + random.uniform(-0.05, 0.05)  # 0.40-0.50
            start_y = 0.75 + random.uniform(-0.05, 0.05)  # 0.70-0.80
            end_x = 0.45 + random.uniform(-0.05, 0.05)    # 0.40-0.50
            end_y = 0.25 + random.uniform(-0.05, 0.05)    # 0.20-0.30
            duration = 0.15 + random.uniform(0, 0.1)      # 0.15-0.25 seconds

            # Convert to pixel coordinates for logging
            info = self.d.info
            width, height = info.get('displayWidth', 1080), info.get('displayHeight', 2400)
            from_x, from_y = int(start_x * width), int(start_y * height)
            to_x, to_y = int(end_x * width), int(end_y * height)

            user_logger.swipe_coordinates(self.serial, from_x, from_y, to_x, to_y)
            self.d.swipe(start_x, start_y, end_x, end_y, duration)

        except Exception as e:
            logger.warning(f"Swipe up failed: {e}")
            user_logger.error_occurred(self.serial, "Swipe", str(e))

    def like(self, prob=0.05) -> bool:
        """Like current video with given probability. Returns True if liked."""
        if random.random() < prob:
            try:
                self.d.click(0.90, 0.55)
                time.sleep(0.2)
                return True
            except Exception as e:
                logger.warning(f"Like failed: {e}")
                user_logger.error_occurred(self.serial, "Like", str(e))
        return False

    def tap_share_then_back(self) -> bool:
        try:
            # naive: tap share on right rail at ~90% height, ~88% width
            self.d.click(0.88, 0.68)
            time.sleep(0.5)
            self.d.press("back")
            return True
        except Exception as e:
            logger.warning(f"Share tap failed: {e}")
            return False

    def toggle_bookmark(self) -> bool:
        try:
            # naive: bookmark below share on right rail
            self.d.click(0.88, 0.80)
            time.sleep(0.2)
            return True
        except Exception as e:
            logger.warning(f"Bookmark toggle failed: {e}")
            return False

    def random_volume_nudge(self) -> str:
        try:
            import random
            if random.random() < 0.5:
                self.d.press("volume_up"); return "up"
            else:
                self.d.press("volume_down"); return "down"
        except Exception as e:
            logger.warning(f"Volume nudge failed: {e}")
            return "error"