"""
Screen monitoring and text detection for Android automation debugging.

This module provides tools to capture and analyze what's visible on the Android screen,
helping to debug stuck states and unknown popups.
"""

from __future__ import annotations
import time
from typing import List, Dict, Any, Optional
from loguru import logger
from utils.user_logger import user_logger

class ScreenMonitor:
    """Monitor Android screen content and detect stuck states."""
    
    def __init__(self, device, serial: str):
        self.device = device
        self.serial = serial
        self.last_screen_state = None
        self.last_page_type = None
        self.stuck_threshold = 10.0  # seconds
        self.last_state_change = time.time()
    
    def get_visible_text(self) -> List[str]:
        """Get all visible text elements on screen."""
        try:
            # Get all text elements
            texts = []
            
            # Try different ways to get text
            for elem in self.device.xpath('//*[@text!=""]').all():
                try:
                    text = elem.get_text() if hasattr(elem, 'get_text') else elem.info.get('text', '')
                    if text and text.strip():
                        texts.append(text.strip())
                except Exception:
                    pass

            # Also try content-desc
            for elem in self.device.xpath('//*[@content-desc!=""]').all():
                desc = elem.info.get('contentDescription', '')
                if desc and desc.strip():
                    texts.append(f"[desc: {desc.strip()}]")
            
            # Remove duplicates while preserving order
            unique_texts = []
            seen = set()
            for text in texts:
                if text not in seen:
                    unique_texts.append(text)
                    seen.add(text)
            
            return unique_texts
            
        except Exception as e:
            logger.warning(f"Failed to get visible text: {e}")
            return []
    
    def get_clickable_elements(self) -> List[Dict[str, Any]]:
        """Get all clickable elements with their text and positions."""
        try:
            clickables = []
            
            for elem in self.device.xpath('//*[@clickable="true"]').all():
                info = elem.info
                text = info.get('text', '')
                desc = info.get('contentDescription', '')
                bounds = info.get('bounds', {})
                
                if text or desc:
                    clickables.append({
                        'text': text,
                        'description': desc,
                        'bounds': bounds,
                        'class': info.get('className', ''),
                        'resource_id': info.get('resourceName', '')
                    })
            
            return clickables
            
        except Exception as e:
            logger.warning(f"Failed to get clickable elements: {e}")
            return []
    
    def detect_page_type(self, texts: List[str]) -> str:
        """Detect what type of page/popup we're on based on visible text."""
        text_lower = [t.lower() for t in texts]
        all_text = ' '.join(text_lower)
        
        # Android system pages
        if any('home' in t or 'launcher' in t for t in text_lower):
            return "HOME_SCREEN"

        if any('settings' in t for t in text_lower) and any('system' in t or 'device' in t for t in text_lower):
            return "ANDROID_SETTINGS"

        # TikTok specific page detection - prioritize feed detection
        if (any('for you' in t for t in text_lower) or
            any('following' in t for t in text_lower) or
            any('home' in t and 'friends' in all_text and 'inbox' in all_text and 'profile' in all_text for t in text_lower) or
            any('like' in t and 'comment' in all_text and 'share' in all_text for t in text_lower)):
            return "FEED"

        if any('log in' in t or 'sign in' in t for t in text_lower):
            return "LOGIN"

        if any('birthday' in t for t in text_lower):
            return "AGE_VERIFICATION"

        if any('allow' in t and 'notification' in all_text for t in text_lower):
            return "NOTIFICATION_PERMISSION"

        if any('allow' in t and ('location' in all_text or 'access' in all_text) for t in text_lower):
            return "LOCATION_PERMISSION"

        if any('terms' in t and 'service' in all_text for t in text_lower):
            return "TERMS_OF_SERVICE"
        
        if any('privacy' in t and 'policy' in all_text for t in text_lower):
            return "PRIVACY_POLICY"

        # Check for ads preferences popup - look for specific text patterns
        if (any('choose how ads are shown' in t for t in text_lower) or
            any('generic ads' in t for t in text_lower) or
            any('personalized ads' in t for t in text_lower) or
            any('ads' in t and ('preference' in all_text or 'personalization' in all_text) for t in text_lower) or
            any('relevant ads' in all_text and 'tiktok free' in all_text for t in text_lower)):
            return "ADS_PREFERENCES"

        # Check for contact sync permission
        if (any('tiktok is more fun with friends' in t for t in text_lower) or
            any('syncing your phone contacts' in t for t in text_lower) or
            any('find and get discovered by people you know' in t for t in text_lower)):
            return "CONTACT_SYNC_PERMISSION"

        if any('got it' in t or 'ok' in t or 'continue' in t for t in text_lower):
            return "GENERIC_DIALOG"

        if any('update' in t and 'app' in all_text for t in text_lower):
            return "APP_UPDATE"

        if any('network' in t or 'connection' in t for t in text_lower):
            return "NETWORK_ERROR"

        if any('try again' in t or 'retry' in t for t in text_lower):
            return "ERROR_DIALOG"

        # Check for specific TikTok screens
        if any('profile' in t for t in text_lower) and any('edit' in all_text or 'follow' in all_text):
            return "PROFILE_PAGE"

        if any('discover' in t or 'search' in t for t in text_lower):
            return "DISCOVER_PAGE"

        return "UNKNOWN"
    
    def print_screen_state(self, force: bool = False):
        """Print current screen state to terminal for debugging."""
        try:
            texts = self.get_visible_text()
            clickables = self.get_clickable_elements()
            page_type = self.detect_page_type(texts)
            
            # Check if screen state changed
            current_state = (tuple(texts), page_type)
            state_changed = current_state != self.last_screen_state
            page_changed = page_type != self.last_page_type

            if state_changed:
                self.last_state_change = time.time()
                self.last_screen_state = current_state

            # Log page detection to user logs when page type changes
            if page_changed:
                if self.last_page_type:
                    user_logger.page_transition(self.serial, self.last_page_type, page_type)
                else:
                    user_logger.page_detected(self.serial, page_type, f"{len(texts)} text elements")
                self.last_page_type = page_type
            
            # Print if forced or when page or state changes significantly to reduce verbosity
            if force or state_changed or page_changed:
                print(f"\n{'='*60}")
                print(f"📱 SCREEN STATE [{self.serial}] - Page: {page_type}")
                print(f"{'='*60}")
                if texts:
                    print("📝 VISIBLE TEXT (truncated to 20 items):")
                    for i, text in enumerate(texts[:20], 1):
                        print(f"  {i:2d}. {text}")
                    if len(texts) > 20:
                        print(f"  ... and {len(texts)-20} more")
                else:
                    print("📝 VISIBLE TEXT: (none)")
                if clickables:
                    print(f"\n🖱️  CLICKABLE ELEMENTS (truncated to 20 of {len(clickables)}):")
                    for i, elem in enumerate(clickables[:20], 1):
                        text = elem['text'] or elem['description'] or '(no text)'
                        bounds = elem['bounds']
                        print(f"  {i:2d}. {text} [{elem['class']}]")
                        if bounds:
                            print(f"      Bounds: {bounds}")
                    if len(clickables) > 20:
                        print(f"  ... and {len(clickables)-20} more")
                else:
                    print(f"\n🖱️  CLICKABLE ELEMENTS: (none)")
                print(f"{'='*60}\n")
                return True  # State changed or page changed
            
            return False  # No change
            
        except Exception as e:
            logger.error(f"Failed to print screen state: {e}")
            return False
    
    def is_stuck(self) -> bool:
        """Check if automation appears to be stuck on current screen."""
        return time.time() - self.last_state_change > self.stuck_threshold
    
    def get_suggested_actions(self, page_type: str, texts: List[str]) -> List[str]:
        """Get suggested actions for the current page type."""
        suggestions = []
        
        if page_type == "NOTIFICATION_PERMISSION":
            suggestions.extend([
                "Click 'Allow' for notifications",
                "Click 'Don't allow' to skip notifications"
            ])
        
        elif page_type == "LOCATION_PERMISSION":
            suggestions.extend([
                "Click 'Don't allow' for location (recommended)",
                "Click 'Allow' if location is needed"
            ])

        elif page_type == "CONTACT_SYNC_PERMISSION":
            suggestions.extend([
                "Click 'Don't allow' to skip contact sync (recommended)",
                "Click 'OK' to allow contact access"
            ])
        
        elif page_type == "AGE_VERIFICATION":
            suggestions.extend([
                "Enter a valid birthdate",
                "Click continue after entering date"
            ])
        
        elif page_type == "ADS_PREFERENCES":
            suggestions.extend([
                "Click 'Generic ads' to select less personalized ads",
                "Click 'Personalized ads' if you prefer targeted ads",
                "Look for 'Select' button under your choice"
            ])

        elif page_type == "TERMS_OF_SERVICE" or page_type == "PRIVACY_POLICY":
            suggestions.extend([
                "Click 'Accept' or 'Agree'",
                "Scroll down to find accept button"
            ])
        
        elif page_type == "GENERIC_DIALOG":
            suggestions.extend([
                "Click 'OK', 'Got it', or 'Continue'",
                "Look for dismiss/close button"
            ])
        
        elif page_type == "APP_UPDATE":
            suggestions.extend([
                "Click 'Later' or 'Skip' to avoid update",
                "Click 'Update' if update is required"
            ])
        
        elif page_type == "LOGIN":
            suggestions.extend([
                "Enter login credentials",
                "Look for 'Skip' or guest options"
            ])
        
        elif page_type == "UNKNOWN":
            # Try to find common action buttons
            text_lower = [t.lower() for t in texts]
            if any('allow' in t for t in text_lower):
                suggestions.append("Consider clicking 'Allow' button")
            if any('deny' in t or "don't allow" in t for t in text_lower):
                suggestions.append("Consider clicking 'Deny' or 'Don't allow'")
            if any('ok' in t or 'got it' in t for t in text_lower):
                suggestions.append("Consider clicking 'OK' or 'Got it'")
            if any('continue' in t for t in text_lower):
                suggestions.append("Consider clicking 'Continue'")
            if any('skip' in t or 'later' in t for t in text_lower):
                suggestions.append("Consider clicking 'Skip' or 'Later'")
            if any('close' in t or 'dismiss' in t for t in text_lower):
                suggestions.append("Consider clicking 'Close' or dismiss button")
        
        return suggestions
    
    def monitor_and_suggest(self):
        """Monitor screen and provide suggestions when stuck."""
        try:
            texts = self.get_visible_text()
            page_type = self.detect_page_type(texts)
            
            # Print current state
            state_changed = self.print_screen_state()
            
            # Check if stuck
            if self.is_stuck():
                stuck_duration = time.time() - self.last_state_change
                print(f"⚠️  AUTOMATION APPEARS STUCK on {page_type}")
                print(f"   Stuck for {stuck_duration:.1f} seconds")

                suggestions = self.get_suggested_actions(page_type, texts)
                if suggestions:
                    print(f"💡 SUGGESTED ACTIONS:")
                    for i, suggestion in enumerate(suggestions, 1):
                        print(f"   {i}. {suggestion}")
                    user_logger.suggestion_provided(self.serial, page_type, suggestions)
                else:
                    print(f"💡 No specific suggestions for {page_type}")
                    print(f"   Consider manual intervention or updating page detection")

                print()
                user_logger.automation_stuck(self.serial, page_type, stuck_duration)

                return True  # Is stuck
            
            return False  # Not stuck
            
        except Exception as e:
            logger.error(f"Monitor failed: {e}")
            return False
