from __future__ import annotations
import os, sys, json, requests, pathlib, time, shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QPushButton, QLabel, QComboBox, QTextEdit, QFileDialog, QCheckBox, QGroupBox,
    QFormLayout, QSpinBox, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QInputDialog, QProgressBar, QDialog, QDialogButtonBox, QListView
)
from PyQt6.QtWidgets import QDoubleSpinBox
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QMovie, QClipboard
from PyQt6.QtGui import QIcon, QAction, QShortcut, QKeySequence

try:
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAVE_MEDIA=True
except Exception:
    HAVE_MEDIA=False

from ui.scrcpy import SCRCPY

API = os.environ.get("API_URL","http://127.0.0.1:8000")
TOKEN = os.environ.get("API_TOKEN", "").strip()
HEADERS = ({"X-API-Token": TOKEN} if TOKEN else {})

class DeviceCombo(QComboBox):
    """QComboBox that refreshes device list when the popup is opened.
    Ensures the combo remains interactive and clickable.
    """
    def __init__(self, loader, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loader = loader
        # Ensure the combo is always interactive
        self.setEnabled(True)
        self.setEditable(False)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setMinimumWidth(200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showPopup(self):
        # Open popup; avoid refreshing while it is open to prevent selection issues
        super().showPopup()
        uilog(f"DeviceCombo showPopup (enabled={self.isEnabled()}, count={self.count()})")
        # Only trigger background refresh when empty to avoid repopulating during user interaction
        if self.count() == 0 and callable(self._loader):
            QTimer.singleShot(50, lambda: self._loader(from_popup=True))

    def mousePressEvent(self, event):
        # Debug: ensure mouse events are received
        uilog(f"DeviceCombo mouse press: {event.button()}, enabled: {self.isEnabled()}")
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        # Allow mouse wheel to change selection
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        # Allow keyboard navigation
        super().keyPressEvent(event)

def uilog(msg: str):
    try:
        p = pathlib.Path("artifacts/ui_debug.log"); p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S") + " " + msg + "\n")
    except Exception:
        pass

def toast(parent: QWidget, text: str, duration_ms: int = 2000):
    """Non-modal toast overlay centered in parent."""
    try:
        if hasattr(parent, "_toast") and parent._toast:
            parent._toast.deleteLater()
    except Exception:
        pass
    lbl = QLabel(text, parent)
    lbl.setStyleSheet("""
        QLabel { background: rgba(0,0,0,200); color: white; padding: 6px 10px; border-radius: 4px; }
    """)
    lbl.adjustSize()
    r = parent.rect(); x=(r.width()-lbl.width())//2; y=(r.height()-lbl.height())//2
    lbl.move(max(0,x), max(0,y)); lbl.show(); lbl.raise_()
    parent._toast = lbl
    QTimer.singleShot(duration_ms, lbl.deleteLater)

def load_qss():
    dark = pathlib.Path("assets/theme_dark.qss")
    light = pathlib.Path("assets/theme_light.qss")
    if dark.exists():
        with open(dark,"r",encoding="utf-8") as f: return f.read()
    return ""

class BusySpinner(QWidget):
    def __init__(self, parent=None, size=18):
        super().__init__(parent)
        h=QHBoxLayout(self); h.setContentsMargins(0,0,0,0)


        self.lbl = QLabel()
        self.lbl.setFixedSize(size, size)
        self.movie = None
        gif_path = pathlib.Path("assets/icons/spinner.gif")
        if gif_path.exists():
            try:
                self.movie = QMovie(str(gif_path))
                self.lbl.setMovie(self.movie)
                self.movie.start()
            except Exception:
                self.movie = None
        if not self.movie:
            # Fallback: indeterminate progress bar
            self.bar = QProgressBar(); self.bar.setRange(0,0); self.bar.setFixedWidth(size*4)
            h.addWidget(self.bar)
        else:
            h.addWidget(self.lbl)
        self.hide()
    def show(self): super().show()
class EmbeddedScrcpyWidget(QWidget):
    """Embeds a running scrcpy window into a Qt widget (Windows only).
    Starts scrcpy with a unique window title and reparents the window here.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._serial = None
        self._title = None
        self._child_hwnd = None
        self._aspect = 9/16  # default portrait aspect
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_child)
        self._poll_resize = QTimer(self)
        self._poll_resize.setInterval(250)
        self._poll_resize.timeout.connect(self._fit_child)
        self._info = QLabel("Device screen not shown. Click 'Show Device Screen' to start.")
        self._info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.addWidget(self._info)

    def is_active(self) -> bool:
        return bool(self._child_hwnd)

    def show_for_device(self, serial: str):
        self._serial = serial
        self._title = f"scrcpy-embed-{serial}"
        # Start scrcpy (no always-on-top, no control to avoid key grabs)
        try:
            SCRCPY.start(serial=serial, control=False, always_on_top=False, max_fps=30, bit_rate="6M", window_title=self._title)
        except Exception as e:
            self._info.setText(f"Could not start scrcpy: {e}")
            return
        self._info.setText("Starting device screen…")
        self._timer.start(500)
        self._poll_resize.start()

    def hide_and_stop(self):
        self._timer.stop(); self._poll_resize.stop()
        self._detach_child()
        try:
            SCRCPY.stop()
        except Exception:
            pass
        self._info.setText("Device screen hidden.")

    def resizeEvent(self, event):  # noqa: D401
        super().resizeEvent(event)
        self._fit_child()

    # --- internal helpers (Windows only) ---
    def _find_hwnd(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = wintypes.HWND
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            hwnd = user32.FindWindowW(None, self._title)
            return hwnd
        except Exception:
            return None

    def _attach_child(self, hwnd):
        try:
            import ctypes
            from ctypes import wintypes
            GWL_STYLE = -16
            WS_CHILD = 0x40000000
            WS_VISIBLE = 0x10000000
            user32 = ctypes.windll.user32
            setparent = user32.SetParent
            setparent.argtypes = [wintypes.HWND, wintypes.HWND]
            setparent.restype = wintypes.HWND
            setlong = ctypes.windll.user32.SetWindowLongW
            getlong = ctypes.windll.user32.GetWindowLongW
            setpos = ctypes.windll.user32.SetWindowPos
            setparent(hwnd, int(self.winId()))
            style = getlong(hwnd, GWL_STYLE)
            setlong(hwnd, GWL_STYLE, style | WS_CHILD | WS_VISIBLE)
            self._child_hwnd = hwnd
            self._fit_child()
        except Exception as e:
            self._info.setText(f"Failed to embed scrcpy window: {e}")

    def _detach_child(self):
        if not self._child_hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            setparent = user32.SetParent
            setparent.argtypes = [wintypes.HWND, wintypes.HWND]
            # Detach to desktop (NULL parent)
            setparent(self._child_hwnd, None)
        except Exception:
            pass
        self._child_hwnd = None

    def _fit_child(self):
        if not self._child_hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes
            MoveWindow = ctypes.windll.user32.MoveWindow
            # Maintain aspect ratio inside current widget rect
            W = max(1, self.width()); H = max(1, self.height())
            # Assume portrait 9:16 unless we can infer otherwise in future
            target_w = W; target_h = int(W * (16/9))
            if target_h > H:
                target_h = H; target_w = int(H * (9/16))
            x = (W - target_w)//2; y = (H - target_h)//2
            MoveWindow(self._child_hwnd, x, y, target_w, target_h, True)
        except Exception:
            pass

    def _poll_child(self):
        # Try to find child window shortly after launch
        if not self._child_hwnd:
            hwnd = self._find_hwnd()
            if hwnd:
                self._attach_child(hwnd)
                self._info.setText("")
        # Detect when scrcpy exits
        if self._child_hwnd and not SCRCPY.running():
            self._info.setText("Device screen stopped.")
            self._detach_child()

    def hide(self): super().hide()

class OverviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Initialize filter state
        self.current_filter = "All Actions"
        v=QVBoxLayout(self)
        top=QHBoxLayout(); top.setSpacing(8); top.setContentsMargins(0,0,0,0)

        # Create device combo with explicit settings
        self.device = DeviceCombo(self.load_devices)
        self.device.setObjectName("deviceCombo")  # For debugging
        self.device.setView(QListView())  # avoid native style popup quirks

        # Other controls
        self.refresh=QPushButton("Refresh"); self.refresh.setToolTip("Refresh connected devices")
        self.btn_test_adb=QPushButton("Test ADB"); self.btn_test_adb.setToolTip("Show adb devices output and errors")
        self.last_refreshed=QLabel("")
        self.open_scrcpy=QPushButton("Open scrcpy"); self.open_scrcpy.setToolTip("Mirror the device")
        self.close_scrcpy=QPushButton("Close")
        self.state_badge = QLabel("Idle")

        # spinner for device loading
        self.spinner = BusySpinner(self); self.spinner.hide()
        self.last_refreshed.setMinimumWidth(110)

        # Add widgets to layout with proper spacing
        top.addWidget(QLabel("Device:"))
        top.addWidget(self.device)
        top.addWidget(self.refresh)
        top.addWidget(self.btn_test_adb)
        top.addWidget(self.last_refreshed)
        top.addWidget(self.spinner)
        top.addWidget(self.open_scrcpy)
        top.addWidget(self.close_scrcpy)
        top.addWidget(QLabel("Status:"))
        top.addWidget(self.state_badge)

        v.addLayout(top)

        quick=QGroupBox("Quick actions")
        fl=QFormLayout(quick)
        self.warm_secs=QSpinBox(); self.warm_secs.setRange(30, 7200); self.warm_secs.setValue(120)
        self.btn_warm=QPushButton("Start Warmup"); self.btn_warm.setShortcut("Ctrl+R")
        # System requirements summary
        req = []
        if not shutil.which("adb"): req.append("adb missing")
        if not shutil.which("scrcpy"): req.append("scrcpy missing")
        # ffmpeg is optional for future repurpose features; not required for core functionality
        if req:
            warn = QLabel("Missing: " + ", ".join(req))
            warn.setStyleSheet("color: #c00; font-weight: bold")
            v.addWidget(warn)

        fl.addRow("Warmup seconds", self.warm_secs); fl.addRow(self.btn_warm)
        v.addWidget(quick)

        # Embedded device screen toggle panel (Windows embedding)
        scr_group = QGroupBox("Device Screen")
        scr_l = QVBoxLayout(scr_group)
        self.scrcpy_embed = EmbeddedScrcpyWidget()
        self.btn_toggle_screen = QPushButton("Show Device Screen")
        self.btn_toggle_screen.setCheckable(True)
        self.btn_toggle_screen.toggled.connect(self._toggle_device_screen)
        scr_l.addWidget(self.scrcpy_embed, 1)
        scr_l.addWidget(self.btn_toggle_screen)
        v.addWidget(scr_group, 1)

        # User-friendly logs display with controls
        logs_group = QGroupBox("UX Logs")
        logs_layout = QVBoxLayout(logs_group)

        # Log control buttons
        log_controls = QHBoxLayout()
        log_controls.setSpacing(8)

        self.btn_clear_logs = QPushButton("Clear Logs")
        self.btn_clear_logs.setMaximumWidth(100)
        self.btn_copy_logs = QPushButton("Copy to Clipboard")
        self.btn_copy_logs.setMaximumWidth(120)
        self.btn_export_logs = QPushButton("Export Logs")
        self.btn_export_logs.setMaximumWidth(100)

        # Log filter controls
        self.log_filter_label = QLabel("Filter:")
        self.log_filter_combo = QComboBox()
        self.log_filter_combo.addItems(["All Actions", "🚀 App Launch", "👀 Video Watching", "📜 Scrolling", "❤️ Likes", "✅ Popups", "🔥 Warmup"])
        self.log_filter_combo.setMaximumWidth(150)

        log_controls.addWidget(self.btn_clear_logs)
        log_controls.addWidget(self.btn_copy_logs)
        log_controls.addWidget(self.btn_export_logs)
        log_controls.addStretch()
        log_controls.addWidget(self.log_filter_label)
        log_controls.addWidget(self.log_filter_combo)

        logs_layout.addLayout(log_controls)

        # Logs text area
        self.logs=QTextEdit(); self.logs.setReadOnly(True); self.logs.setMinimumHeight(240)
        self.logs.setStyleSheet("QTextEdit { font-family: 'Consolas', 'Monaco', monospace; font-size: 11px; }")
        logs_layout.addWidget(self.logs, 1)

        v.addWidget(logs_group, 1)

        self.refresh.clicked.connect(self.load_devices)
        self.btn_test_adb.clicked.connect(self._test_adb)
        self.open_scrcpy.clicked.connect(self.open_scrcpy_clicked)
        self.close_scrcpy.clicked.connect(lambda: SCRCPY.stop())
        self.btn_warm.clicked.connect(self.toggle_warmup)

        # Connect log management buttons
        self.btn_clear_logs.clicked.connect(self.clear_logs)
        self.btn_copy_logs.clicked.connect(self.copy_logs_to_clipboard)
        self.btn_export_logs.clicked.connect(self.export_logs)
        self.log_filter_combo.currentTextChanged.connect(self.filter_logs)

        # Track last warmup job id and progress
        self._warmup_job_id = None
        self._warmup_start_ms = None
        self._warmup_target_s = None

        self.load_devices()

        # Add a test button to verify dropdown functionality
        def test_dropdown():
            uilog(f"TestDropdown: count={self.device.count()}, enabled={self.device.isEnabled()}, text={self.device.currentText()!r}")
            print(f"Test: Device combo has {self.device.count()} items")
            print(f"Test: Current selection: {self.device.currentText()} -> {self.device.currentData()}")
            print(f"Test: Combo enabled: {self.device.isEnabled()}")
            # Always open to verify popup rendering even when empty
            self.device.showPopup()

        self.test_btn = QPushButton("Test Dropdown")
        self.test_btn.clicked.connect(test_dropdown)
        top.addWidget(self.test_btn)

        # Debug screen button
        self.debug_screen_btn = QPushButton("Debug Screen")
        self.debug_screen_btn.setToolTip("Print current Android screen state to terminal")
        self.debug_screen_btn.clicked.connect(self.debug_screen_state)
        top.addWidget(self.debug_screen_btn)

        # Handle ads popup button
        self.ads_btn = QPushButton("Fix Ads Popup")
        self.ads_btn.setToolTip("Click 'Generic ads' and 'Select' to dismiss TikTok ads preferences")
        self.ads_btn.clicked.connect(self.handle_ads_popup)
        top.addWidget(self.ads_btn)

        # Handle contact sync popup button
        self.contact_btn = QPushButton("Deny Contacts")
        self.contact_btn.setToolTip("Click 'Don't allow' to deny TikTok contact sync permission")
        self.contact_btn.clicked.connect(self.handle_contact_sync)
        top.addWidget(self.contact_btn)
        # Reduce polling frequency to lower API load: logs every ~2.5s
        self.t=QTimer(self); self.t.timeout.connect(self._tick); self.t.start(2500); self.pull_logs()

    def _set_last_refreshed(self):
        self.last_refreshed.setText(time.strftime("last refresh: %H:%M:%S"))

    # ---------- async helpers ----------
    def _async_call(self, fn, cb):
        import threading
        uilog("Overview._async_call: starting thread")
        def _run():
            try:
                uilog("Overview._async_call: calling fn()")
                res = fn()
                uilog(f"Overview._async_call: fn() returned {type(res)}")
            except Exception as e:
                uilog(f"Overview._async_call: fn() raised {e!r}")
                res = e
            uilog("Overview._async_call: using QTimer.singleShot for callback")
            # Store callback and result for the timer
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()

    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            uilog("Overview._execute_pending_callback: executing callback on main thread")
            try:
                cb(res)
                uilog("Overview._execute_pending_callback: callback completed successfully")
            except Exception as e:
                uilog(f"Overview._execute_pending_callback: callback raised {e!r}")



    def curdev(self):
        # Return the device serial (stored in itemData), not the display text
        serial = self.device.currentData()
        return serial if serial else None

    def load_devices(self, from_popup: bool = False):
        uilog(f"Overview.load_devices: STARTING (from_popup={from_popup})")
        # Show spinner but NEVER disable the device combo to keep it clickable
        self.spinner.show()
        if not from_popup:
            self.refresh.setEnabled(False)

        def fn():
            return requests.get(f"{API}/devices", timeout=10, headers=HEADERS)

        def done(res):
            try:
                uilog("Overview.load_devices: response received")
                if isinstance(res, Exception):
                    raise res
                res.raise_for_status(); devs = res.json()
                uilog(f"Overview.load_devices: HTTP {res.status_code}, devs={len(devs)}")
                toast(self, f"Found {len(devs)} device{'s' if len(devs)!=1 else ''}")
            except Exception as e:
                uilog(f"Overview.load_devices: ERROR {e!r}")
                QMessageBox.warning(self, "Devices", f"Could not fetch devices. {e}"); devs = []
            finally:
                self.spinner.hide()
                if not from_popup:
                    self.refresh.setEnabled(True)
                self._set_last_refreshed()
            # Populate device list
            self.device.clear()
            uilog(f"Populating device combo with {len(devs)} devices")
            for d in devs:
                serial = d.get("serial", "?")
                label = serial
                st = d.get("state", "?")
                mdl = d.get("model", ""); ver = d.get("android", "")
                if st and st != "device":
                    label += f" ({st})"
                if mdl or ver:
                    label += f"  [{mdl} {ver}]"
                self.device.addItem(label, serial)
                uilog(f"Added device: {label} -> {serial}")

            # Auto-select device
            preferred = os.environ.get("DEVICE_SERIAL", "").strip()
            idx = -1
            if preferred:
                for i in range(self.device.count()):
                    if self.device.itemData(i) == preferred:
                        idx = i; break
            if idx == -1 and self.device.count() == 1:
                idx = 0
            if idx >= 0:
                self.device.setCurrentIndex(idx)
                uilog(f"Auto-selected device at index {idx}: {self.device.currentText()}")

            # Ensure combo remains enabled and interactive
            self.device.setEnabled(True)
            uilog(f"Device combo state: enabled={self.device.isEnabled()}, count={self.device.count()}")

        self._async_call(fn, done)
    def _toggle_device_screen(self, checked: bool):
        d = self.curdev()
        if checked:
            if not d:
                QMessageBox.warning(self, "Device Screen", "Select a device first")
                self.btn_toggle_screen.setChecked(False)
                return
            self.btn_toggle_screen.setText("Hide Device Screen")
            self.scrcpy_embed.show_for_device(d)
        else:
            self.btn_toggle_screen.setText("Show Device Screen")
            self.scrcpy_embed.hide_and_stop()


    def pull_logs(self):
        def fn():
            # Get user-friendly logs instead of technical logs
            device_serial = self.device.currentData() if self.device.currentData() else None
            params = {"limit": 50}
            if device_serial:
                params["device"] = device_serial
            return requests.get(f"{API}/logs/user", params=params, timeout=10, headers=HEADERS)
        def done(res):
            try:
                if not isinstance(res, Exception) and res.status_code==200:
                    data = res.json()
                    logs = data.get("logs", [])
                    uilog(f"Overview._load_logs: received {len(logs)} user logs from API")
                    if logs:
                        uilog(f"Overview._load_logs: first log entry: {logs[0]}")
                    else:
                        uilog("Overview._load_logs: logs array is empty")
                    if logs:
                        # Apply filter if set
                        filtered_logs = logs
                        if hasattr(self, 'current_filter') and self.current_filter != "All Actions":
                            filter_map = {
                                "🚀 App Launch": ["TikTok Launched"],
                                "👀 Video Watching": ["Watching Video", "Video Watched"],
                                "📜 Scrolling": ["Scrolling", "Scroll", "Video Scrolled"],
                                "❤️ Likes": ["Like", "Liked"],
                                "✅ Popups": ["Popup Dismissed"],
                                "🔥 Warmup": ["Warmup"]
                            }
                            if self.current_filter in filter_map:
                                keywords = filter_map[self.current_filter]
                                filtered_logs = [log for log in logs if any(keyword in log.get("action", "") for keyword in keywords)]

                        # Format user-friendly logs
                        formatted_logs = []
                        for log in filtered_logs:
                            timestamp = log.get("timestamp", "")
                            device = log.get("device", "")
                            action = log.get("action", "")
                            details = log.get("details", "")
                            status = log.get("status", "info")

                            # Color coding based on status
                            if status == "success":
                                line = f"[{timestamp}] {device}: {action}"
                            elif status == "error":
                                line = f"[{timestamp}] {device}: {action}"
                            elif status == "warning":
                                line = f"[{timestamp}] {device}: {action}"
                            else:
                                line = f"[{timestamp}] {device}: {action}"

                            if details:
                                line += f" - {details}"
                            formatted_logs.append(line)

                        log_text = "\n".join(formatted_logs)
                        uilog(f"Overview._load_logs: setting logs text ({len(log_text)} chars)")
                        self.logs.setPlainText(log_text)
                    else:
                        uilog("Overview._load_logs: no logs received, showing default message")
                        self.logs.setPlainText("No automation activity yet. Start a warmup to see logs here.")
                else:
                    # Fallback to technical logs only if Logs tab is visible
                    if self.isVisible():
                        tech_res = requests.get(f"{API}/logs",params={"source":"all","lines":200},timeout=10, headers=HEADERS)
                        if tech_res.status_code == 200:
                            self.logs.setPlainText(tech_res.text)
            except Exception as e:
                # Fallback to technical logs
                try:
                    tech_res = requests.get(f"{API}/logs",params={"source":"all","lines":200},timeout=10, headers=HEADERS)
                    if tech_res.status_code == 200:
                        self.logs.setPlainText(tech_res.text)
                except Exception:
                    pass
        self._async_call(fn, done)

    def _tick(self):
        # logs refresh (less often)
        self.pull_logs()
        # warmup progress indicator
        if self._warmup_job_id and self._warmup_target_s and self._warmup_start_ms:
            elapsed = int(time.time()*1000) - self._warmup_start_ms
            pct = max(0, min(100, int((elapsed/1000) / self._warmup_target_s * 100)))
            self.btn_warm.setText(f"Stop Warmup ({pct}%)")
            # check if job finished
            def fn():
                return requests.get(f"{API}/jobs/{self._warmup_job_id}", timeout=10, headers=HEADERS)

            def done(res):
                try:
                    if not isinstance(res, Exception) and res.status_code==200:
                        st = res.json().get("status")
                        if st in ("done","failed","cancelled"):
                            self._warmup_job_id=None; self._warmup_start_ms=None; self._warmup_target_s=None
                            self.btn_warm.setText("Start Warmup")
                except Exception:
                    pass
            self._async_call(fn, done)

        # update device state badge
        status = []
        if self._warmup_job_id:
            status.append("running")
        if SCRCPY.running():
            status.append("scrcpy")
        self.state_badge.setText(" | ".join(status) if status else "idle")

    def open_scrcpy_clicked(self):
        d=self.curdev()
        if not d: QMessageBox.warning(self,"scrcpy","Select device"); return
        try: SCRCPY.start(serial=d, control=False, turn_screen_off=False, max_fps=30, bit_rate="8M",
                          window_title=f"scrcpy - {d}", always_on_top=True)
        except Exception as e: QMessageBox.warning(self,"scrcpy",str(e))

    def debug_screen_state(self):
        """Debug current screen state for the selected device."""
        device = self.curdev()
        if not device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return

        def fn():
            return requests.post(f"{API}/debug/screen", json={"device_serial": device}, timeout=15, headers=HEADERS)

        def done(res):
            try:
                if isinstance(res, Exception):
                    QMessageBox.warning(self, "Error", f"Screen debug failed: {res}")
                elif res.status_code == 200:
                    data = res.json()
                    page_type = data.get("page_type", "UNKNOWN")
                    suggestions = data.get("suggestions", [])
                    is_stuck = data.get("is_stuck", False)

                    msg = f"Screen debug completed for {device}\n\n"
                    msg += f"Page Type: {page_type}\n"
                    msg += f"Is Stuck: {is_stuck}\n\n"

                    if suggestions:
                        msg += "Suggestions:\n"
                        for i, suggestion in enumerate(suggestions, 1):
                            msg += f"{i}. {suggestion}\n"
                    else:
                        msg += "No specific suggestions available.\n"

                    msg += "\nCheck terminal for detailed screen state output."

                    QMessageBox.information(self, "Screen Debug", msg)
                else:
                    QMessageBox.warning(self, "Error", f"Screen debug failed: HTTP {res.status_code}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to process screen debug: {e}")

        self._async_call(fn, done)

    def handle_ads_popup(self):
        """Handle TikTok ads preferences popup by clicking Generic ads and Select."""
        device = self.curdev()
        if not device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return

        def fn():
            return requests.post(f"{API}/debug/click-generic-ads", json={"device_serial": device}, timeout=10, headers=HEADERS)

        def done(res):
            try:
                if isinstance(res, Exception):
                    QMessageBox.warning(self, "Error", f"Failed to handle ads popup: {res}")
                elif res.status_code == 200:
                    data = res.json()
                    if data.get("ok"):
                        action = data.get("action", "Completed")
                        QMessageBox.information(self, "Success", f"Ads popup handled: {action}")
                    else:
                        error = data.get("error", "Unknown error")
                        QMessageBox.warning(self, "Failed", f"Could not handle ads popup: {error}")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to handle ads popup: HTTP {res.status_code}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to process response: {e}")

        self._async_call(fn, done)

    def handle_contact_sync(self):
        """Handle TikTok contact sync popup by clicking Don't allow."""
        device = self.curdev()
        if not device:
            QMessageBox.warning(self, "No Device", "Please select a device first")
            return

        def fn():
            return requests.post(f"{API}/debug/deny-contact-sync", json={"device_serial": device}, timeout=10, headers=HEADERS)

        def done(res):
            try:
                if isinstance(res, Exception):
                    QMessageBox.warning(self, "Error", f"Failed to handle contact sync: {res}")
                elif res.status_code == 200:
                    data = res.json()
                    if data.get("ok"):
                        action = data.get("action", "Completed")
                        QMessageBox.information(self, "Success", f"Contact sync handled: {action}")
                    else:
                        error = data.get("error", "Unknown error")
                        QMessageBox.warning(self, "Failed", f"Could not handle contact sync: {error}")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to handle contact sync: HTTP {res.status_code}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to process response: {e}")

        self._async_call(fn, done)

    def _test_adb(self):
        def fn():
            return requests.get(f"{API}/debug/adb", timeout=10, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                data = res.json() if res.status_code==200 else {"error": res.text}
            except Exception as e:
                data = {"error": str(e)}
            dlg = QDialog(self); dlg.setWindowTitle("ADB Diagnostics"); dlg.resize(820, 480)
            lay = QVBoxLayout(dlg)
            txt = QTextEdit(); txt.setReadOnly(True)
            txt.setPlainText(json.dumps(data, indent=2))
            lay.addWidget(txt)
            bb = QDialogButtonBox(QDialogButtonBox.Ok); bb.accepted.connect(dlg.accept)
            lay.addWidget(bb)
            dlg.exec_()
        self._async_call(fn, done)

    def toggle_warmup(self):
        d=self.curdev()
        if not d:
            toast(self,"Select a device first"); return
        # If a warmup is running, request cancel
        if self._warmup_job_id:
            try:
                requests.post(f"{API}/jobs/{self._warmup_job_id}/cancel", timeout=5, headers=HEADERS)
                self._warmup_job_id=None; self._warmup_start_ms=None; self._warmup_target_s=None
                self.btn_warm.setText("Start Warmup")
                toast(self, "Warmup cancel requested")
            except Exception as e:
                QMessageBox.warning(self,"Cancel",str(e))
            return
        secs=int(self.warm_secs.value())
        try:
            r=requests.post(f"{API}/enqueue/warmup",json={"device_serial":d,"seconds":secs}, timeout=10, headers=HEADERS)
            if r.status_code==200:
                jid = r.json().get("job_id")
                self._warmup_job_id = jid
                self._warmup_start_ms = int(time.time()*1000)
                self._warmup_target_s = secs
                self.btn_warm.setText("Stop Warmup")
                toast(self, f"Warmup enqueued: #{jid}")
            else:
                QMessageBox.warning(self,"Error",f"{r.status_code}: {r.text}")
        except Exception as e:
            QMessageBox.warning(self,"Error",str(e))

    def clear_logs(self):
        """Clear user logs (for selected device if chosen, otherwise all)."""
        try:
            device_serial = self.device.currentData() if self.device.currentData() else None
            params = {"device": device_serial} if device_serial else None
            response = requests.delete(f"{API}/logs/user", params=params, headers=HEADERS)
            if response.status_code == 200:
                uilog("✅ Logs cleared successfully")
                self.logs.clear()
                # Refresh the logs display immediately
                self.pull_logs()
            else:
                uilog(f"❌ Failed to clear logs: {response.status_code}")
        except Exception as e:
            uilog(f"❌ Error clearing logs: {e}")

    def copy_logs_to_clipboard(self):
        """Copy current logs to clipboard."""
        try:
            logs_text = self.logs.toPlainText()
            clipboard = QApplication.clipboard()
            clipboard.setText(logs_text)
            uilog("✅ Logs copied to clipboard")
        except Exception as e:
            uilog(f"❌ Error copying logs: {e}")

    def export_logs(self):
        """Export logs to file."""
        try:
            from PyQt6.QtWidgets import QFileDialog
            filename, _ = QFileDialog.getSaveFileName(
                self, "Export Logs", "user_logs.txt", "Text Files (*.txt);;All Files (*)"
            )
            if filename:
                logs_text = self.logs.toPlainText()
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(logs_text)
                uilog(f"✅ Logs exported to {filename}")
        except Exception as e:
            uilog(f"❌ Error exporting logs: {e}")

    def filter_logs(self, filter_text):
        """Filter logs based on selected criteria."""
        uilog(f"🔍 Filter changed to: {filter_text}")

        # Store the current filter
        self.current_filter = filter_text

        # Reload logs with the filter applied
        self._load_logs()

class QuickRunPage(QWidget):
    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            try:
                cb(res)
            except Exception as e:
                uilog(f"QuickRunPage._execute_pending_callback: callback raised {e!r}")

    def _async_call(self, fn, cb):
        import threading
        def _run():
            try:
                res = fn()
            except Exception as e:
                res = e
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()
    def __init__(self, parent=None):
        super().__init__(parent)
        v=QVBoxLayout(self)
        row=QHBoxLayout()
        self.goal=QComboBox(); self.goal.addItems(["Warmup","Post","Full Pipeline"])
        self.device=DeviceCombo(self.load_devices)
        self.btn_dev=QPushButton("Refresh")
        # last refresh + spinner next to device picker
        self.last_refreshed = QLabel("")
        self.spinner = BusySpinner(self); self.spinner.hide()
        # Place these after the device control for consistent layout
        for w in (QLabel("Goal:"), self.goal, QLabel("Device:"), self.device, self.btn_dev, self.last_refreshed, self.spinner):
            row.addWidget(w)
        v.addLayout(row)

        content=QGroupBox("Content")
        cf=QHBoxLayout(content)
        self.video_path=QLineEdit(); self.video_path.setPlaceholderText("Select video for Post…")
        self.pick_video=QPushButton("Browse…"); self.pick_video.setToolTip("Pick a video file to post")
        self.repurpose=QCheckBox("Repurpose to 9:16 before post")
        self.preview_btn=QPushButton("Preview")
        cf.addWidget(self.video_path,1); cf.addWidget(self.pick_video); cf.addWidget(self.repurpose); cf.addWidget(self.preview_btn)
        v.addWidget(content)

        safety=QGroupBox("Safety preset")
        sf=QHBoxLayout(safety)
        self.preset=QComboBox(); self.preset.addItems(["Safe","Balanced","Pushy"])
        self.like_prob=QSpinBox(); self.like_prob.setRange(0,100); self.like_prob.setValue(7)
        self.watch_lo=QSpinBox(); self.watch_lo.setRange(1,60); self.watch_lo.setValue(6)
        self.watch_hi=QSpinBox(); self.watch_hi.setRange(2,120); self.watch_hi.setValue(13)
        for w in (QLabel("Preset"), self.preset, QLabel("Like %"), self.like_prob, QLabel("Watch s"), self.watch_lo, QLabel("–"), self.watch_hi): sf.addWidget(w)
        v.addWidget(safety)

        act=QHBoxLayout()
        self.launch=QPushButton("Run now")
        self.auto_scrcpy=QCheckBox("Auto-open scrcpy when running"); self.auto_scrcpy.setChecked(False)
        act.addWidget(self.launch); act.addWidget(self.auto_scrcpy); act.addStretch(1)
        v.addLayout(act)

        # Non-modal embedded preview (does not overlay UI)
        self.preview_box=QGroupBox("Video Preview")
        pv=QVBoxLayout(self.preview_box)
        if HAVE_MEDIA:
            self.video_widget=QVideoWidget()
            self.media=QMediaPlayer()
            self.audio=QAudioOutput()
            self.media.setAudioOutput(self.audio)
            self.media.setVideoOutput(self.video_widget)
            pv.addWidget(self.video_widget,1)
        else:
            pv.addWidget(QLabel("QtMultimedia not available; preview disabled."))
        v.addWidget(self.preview_box,2)

        self.btn_dev.clicked.connect(self.load_devices)
        self.pick_video.clicked.connect(self.pick_video_dialog)
        self.preview_btn.clicked.connect(self.preview_video)
        self.launch.clicked.connect(self.run_now)
        self.load_devices()

    def load_devices(self, from_popup: bool = False):
        # spinner & disable (not when coming from popup open)
        if not from_popup:
            self.spinner.show(); self.device.setEnabled(False); self.btn_dev.setEnabled(False)
        self.last_refreshed.setText("")
        def fn():
            delay=0.4; last=None
            for _ in range(3):
                try:
                    r=requests.get(f"{API}/devices",timeout=8, headers=HEADERS)
                    if r.status_code==200: return r
                    if r.status_code in (500,502,503,504): time.sleep(delay); delay*=2; continue
                    return r
                except Exception as e:
                    last=e; time.sleep(delay); delay*=2
            return last or RuntimeError("Unknown error")
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                res.raise_for_status(); devs=res.json(); toast(self, f"Found {len(devs)} device{'s' if len(devs)!=1 else ''}")
            except Exception as e:
                QMessageBox.warning(self,"Devices",f"Could not fetch devices. {e}"); devs=[]
            finally:
                self.spinner.hide(); self.device.setEnabled(True); self.btn_dev.setEnabled(True); self.last_refreshed.setText(time.strftime("%H:%M:%S"))
            self.device.clear();
            for d in devs:
                serial = d.get("serial","?")
                self.device.addItem(serial, serial)
            preferred=os.environ.get("DEVICE_SERIAL","" ).strip()
            if preferred:
                i = self.device.findData(preferred)
                if i>=0: self.device.setCurrentIndex(i)
            elif self.device.count()==1:
                self.device.setCurrentIndex(0)
        self._async_call(fn, done)

    def pick_video_dialog(self):
        fn,_=QFileDialog.getOpenFileName(self,"Pick video","","Video files (*.mp4 *.mov *.mkv)")
        if fn: self.video_path.setText(fn)

    def preview_video(self):
        if not HAVE_MEDIA:
            QMessageBox.information(self,"Preview","QtMultimedia not available"); return
        p=self.video_path.text().strip()
        if not p: QMessageBox.information(self,"Preview","Pick a video first"); return
        self.media.setSource(QUrl.fromLocalFile(p)); self.media.play()

    def run_now(self):
        d=self.device.currentData()
        if not d: QMessageBox.warning(self,"Missing","Select a device"); return
        if self.auto_scrcpy.isChecked() and not SCRCPY.running():
            try: SCRCPY.start(serial=d, control=False, always_on_top=True, max_fps=30, bit_rate="6M", window_title=f"scrcpy - {d}")
            except Exception as e: QMessageBox.warning(self,"scrcpy",f"Could not open scrcpy: {e}")
        goal=self.goal.currentText()
        def fn():
            if goal=="Warmup":
                secs = max(int(self.watch_lo.value()*10), 60)
                return requests.post(f"{API}/enqueue/warmup",json={"device_serial":d,"seconds":secs}, headers=HEADERS)
            elif goal=="Post":
                vp=self.video_path.text().strip()
                if not vp:
                    return ValueError("Pick a video file first")
                steps=[{"type":"post_video","video":vp,"caption":""}]
                return requests.post(f"{API}/enqueue/pipeline",json={"device_serial":d,"steps":steps,"repeat":1,"sleep_between":[2,5]}, headers=HEADERS)
            else:
                vp=self.video_path.text().strip()
                if not vp:
                    return ValueError("Pick a video file first")
                steps=[{"type":"rotate_identity","soft":True},{"type":"warmup","duration":max(60,int(self.watch_lo.value()*10))},{"type":"post_video","video":vp}]

        def done(res):
            try:
                if isinstance(res, Exception): raise res
                if isinstance(res, ValueError): raise res
                if res.status_code==200: toast(self,f"Enqueued: {res.json()}")
                else: QMessageBox.warning(self,"Error",f"{res.status_code}: {res.text}")
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
        self._async_call(fn, done)

class PipelinesPage(QWidget):
    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            try:
                cb(res)
            except Exception as e:
                uilog(f"PipelinesPage._execute_pending_callback: callback raised {e!r}")

    def _async_call(self, fn, cb):
        import threading
        def _run():
            try:
                res = fn()
            except Exception as e:
                res = e
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()
    def __init__(self, parent=None):
        super().__init__(parent)
        v=QVBoxLayout(self)
        row=QHBoxLayout()
        self.device=DeviceCombo(self.load_devices); self.refresh=QPushButton("Refresh devices")
        row.addWidget(QLabel("Device:")); row.addWidget(self.device); row.addWidget(self.refresh); row.addStretch(1)
        v.addLayout(row)


        # Add last-refreshed and spinner to the row
        self.last_refreshed = QLabel(""); self.last_refreshed.setMinimumWidth(110)
        self.spinner = BusySpinner(self); self.spinner.hide()
        self.device.setMinimumWidth(220); self.device.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        row.addWidget(self.last_refreshed); row.addWidget(self.spinner)

        self.steps_edit=QTextEdit()
        self.steps_edit.setPlaceholderText('''Example steps JSON:
[{"type":"warmup","duration":120},
 {"type":"post_video","video":"C:/video.mp4","caption":"Hello"},
 {"type":"break","duration":60}]''')
        v.addWidget(self.steps_edit,2)

        controls=QHBoxLayout()
        self.repeat=QSpinBox(); self.repeat.setRange(1,50); self.repeat.setValue(1)
        self.run_btn=QPushButton("Run pipeline")
        controls.addWidget(QLabel("Repeat")); controls.addWidget(self.repeat); controls.addStretch(1); controls.addWidget(self.run_btn)
        v.addLayout(controls)

        self.refresh.clicked.connect(self.load_devices)
        self.run_btn.clicked.connect(self.run_pipeline)
        self.load_devices()

    def load_devices(self, from_popup: bool = False):
        # spinner & disable (not when coming from popup open)
        if not from_popup:
            self.spinner.show(); self.device.setEnabled(False); self.refresh.setEnabled(False)
        self.last_refreshed.setText("")
        def fn():
            return requests.get(f"{API}/devices",timeout=5, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                res.raise_for_status(); devs=res.json()
                toast(self, f"Found {len(devs)} device{'s' if len(devs)!=1 else ''}")
            except Exception as e:
                QMessageBox.warning(self,"Devices",f"Could not fetch devices: {e}"); devs=[]
            finally:
                self.spinner.hide(); self.device.setEnabled(True); self.refresh.setEnabled(True); self.last_refreshed.setText(time.strftime("%H:%M:%S"))
            self.device.clear()
            for d in devs:
                serial=d.get("serial","?")
                label=serial
                st=d.get("state","?")
                if st and st!="device": label += f" ({st})"
                self.device.addItem(label, serial)
            preferred=os.environ.get("DEVICE_SERIAL","" ).strip()
            if preferred:
                i=self.device.findData(preferred)
                if i>=0: self.device.setCurrentIndex(i)
            elif self.device.count()==1:
                self.device.setCurrentIndex(0)
        self._async_call(fn, done)

    def run_pipeline(self):
        d=self.device.currentData()
        if not d: QMessageBox.warning(self,"Missing","Select device"); return
        try:
            steps=json.loads(self.steps_edit.toPlainText().strip() or "[]")
        except Exception as e:
            QMessageBox.warning(self,"JSON","Invalid steps JSON"); return
        def fn():
            return requests.post(f"{API}/enqueue/pipeline",json={"device_serial":d,"steps":steps,"repeat":int(self.repeat.value()),"sleep_between":[2,5]}, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                if res.status_code==200: toast(self, f"Enqueued: {res.json()}")
                else: QMessageBox.warning(self,"Error",f"{res.status_code}: {res.text}")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
        self._async_call(fn, done)

class SchedulesPage(QWidget):
    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            try:
                cb(res)
            except Exception as e:
                uilog(f"SchedulesPage._execute_pending_callback: callback raised {e!r}")

    def _async_call(self, fn, cb):
        import threading
        def _run():
            try:
                res = fn()
            except Exception as e:
                res = e
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()
    def __init__(self, parent=None):
        super().__init__(parent)
        v=QVBoxLayout(self)
        # Top controls: device + run now
        top=QHBoxLayout()
        self.device=DeviceCombo(self.load_devices); self.btn_refresh_dev=QPushButton("Refresh devices")
        self.btn_run_now=QPushButton("Run Now (Flatten & Enqueue)")
        # last-refreshed + spinner
        self.last_refreshed = QLabel(""); self.last_refreshed.setMinimumWidth(110)
        self.spinner = BusySpinner(self); self.spinner.hide()
        top.addWidget(QLabel("Device:")); top.addWidget(self.device); top.addWidget(self.btn_refresh_dev); top.addWidget(self.last_refreshed); top.addWidget(self.spinner); top.addStretch(1); top.addWidget(self.btn_run_now)
        v.addLayout(top)

        self.text=QTextEdit(); self.text.setPlaceholderText("Schedules (read-only view). 'Run Now' flattens selected schedule from config into a pipeline for the chosen device.")
        v.addWidget(self.text,1)
        # Simple selector for schedule name
        selrow=QHBoxLayout(); self.sel_sched=QComboBox(); selrow.addWidget(QLabel("Schedule:")); selrow.addWidget(self.sel_sched); v.addLayout(selrow)

        # Schedules view can refresh less frequently
        self.t=QTimer(self); self.t.timeout.connect(self.refresh); self.t.start(5000); self.refresh()
        self.btn_refresh_dev.clicked.connect(self.load_devices)
        self.btn_run_now.clicked.connect(self.run_now)
        self.load_devices()

    def refresh(self):
        def fn():
            return requests.get(f"{API}/config/schedules",timeout=5, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                data = res.json() if res.status_code==200 else {}
                self.text.setPlainText(json.dumps(data, indent=2))
                cur=self.sel_sched.currentText()
                self.sel_sched.blockSignals(True)
                self.sel_sched.clear()
                for name in data.keys(): self.sel_sched.addItem(name)
                if cur:
                    idx = self.sel_sched.findText(cur)
                    if idx>=0: self.sel_sched.setCurrentIndex(idx)
                self.sel_sched.blockSignals(False)
            except Exception as e:
                self.text.setPlainText(str(e))
        self._async_call(fn, done)

    def load_devices(self, from_popup: bool = False):
        # spinner & disable (not when coming from popup open)
        if not from_popup:
            self.spinner.show(); self.device.setEnabled(False); self.btn_refresh_dev.setEnabled(False)
        self.last_refreshed.setText("")
        def fn():
            return requests.get(f"{API}/devices",timeout=5, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                res.raise_for_status(); devs=res.json()
                toast(self, f"Found {len(devs)} device{'s' if len(devs)!=1 else ''}")
            except Exception:
                devs=[]
            finally:
                self.spinner.hide(); self.device.setEnabled(True); self.btn_refresh_dev.setEnabled(True); self.last_refreshed.setText(time.strftime("%H:%M:%S"))
            self.device.clear()
            for d in devs:
                label = d.get("serial","?")
                if d.get("state") and d["state"]!="device": label += f" ({d['state']})"
                self.device.addItem(label, d.get("serial",""))
            preferred=os.environ.get("DEVICE_SERIAL","" ).strip()
            if preferred:
                i=self.device.findData(preferred)
                if i>=0: self.device.setCurrentIndex(i)
            elif self.device.count()==1:
                self.device.setCurrentIndex(0)
        self._async_call(fn, done)

    def _flatten_schedule(self, cfg_schedules: dict, cfg_cycles: dict, name: str):
        s = cfg_schedules.get(name) or {}
        items = s.get("items", [])
        steps=[]
        for it in items:
            if it.get("type")=="cycle":
                cyc = cfg_cycles.get(it.get("name","")) or {}
                steps.extend(cyc.get("steps", []))
            elif it.get("type")=="break":
                mins = int(it.get("minutes",10))
                steps.append({"type":"break","duration":mins*60})
        return steps

    def run_now(self):
        serial = self.device.currentData() or self.device.currentText().split(" ")[0]
        if not serial:
            QMessageBox.warning(self,"Run Now","Select a device first"); return
        sched_name = self.sel_sched.currentText().strip()
        if not sched_name:
            QMessageBox.warning(self,"Run Now","No schedule selected"); return
        try:
            s = requests.get(f"{API}/config/schedules", timeout=5, headers=HEADERS).json()
            c = requests.get(f"{API}/config/cycles", timeout=5, headers=HEADERS).json()
            steps = self._flatten_schedule(s, c, sched_name)
            if not steps:
                QMessageBox.information(self,"Run Now","No steps for this schedule"); return
            r = requests.post(f"{API}/enqueue/pipeline", json={"device_serial":serial, "steps":steps, "repeat":1, "sleep_between":[2,5]}, timeout=10)
            if r.status_code==200:
                toast(self, f"Enqueued: {r.json()}")
            else:
                QMessageBox.warning(self,"Run Now", f"{r.status_code}: {r.text}")
        except Exception as e:
            QMessageBox.warning(self,"Run Now", str(e))

class DevicesPage(QWidget):
    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            try:
                cb(res)
            except Exception as e:
                uilog(f"DevicesPage._execute_pending_callback: callback raised {e!r}")

    def _async_call(self, fn, cb):
        import threading
        def _run():
            try:
                res = fn()
            except Exception as e:
                res = e
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()
    def __init__(self, parent=None):
        super().__init__(parent)
        v=QVBoxLayout(self)
        self.text=QTextEdit(); self.text.setReadOnly(True)
        v.addWidget(self.text,1)
        # Devices view polling reduced to 5s
        self.t=QTimer(self); self.t.timeout.connect(self.refresh); self.t.start(5000); self.refresh()

    def refresh(self):
        def fn():
            return requests.get(f"{API}/devices",timeout=5, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                res.raise_for_status()
                self.text.setPlainText(json.dumps(res.json(), indent=2))
            except Exception as e:
                self.text.setPlainText(str(e))
        self._async_call(fn, done)

class JobsPage(QWidget):
    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            try:
                cb(res)
            except Exception as e:
                uilog(f"JobsPage._execute_pending_callback: callback raised {e!r}")

    def _async_call(self, fn, cb):
        import threading
        def _run():
            try:
                res = fn()
            except Exception as e:
                res = e
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()
    def __init__(self, parent=None):
        super().__init__(parent)
        h=QHBoxLayout(self)
        left=QVBoxLayout(); right=QVBoxLayout()
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(["ID","Device","Type","Status","Created"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        left.addWidget(self.table,1)
        act=QHBoxLayout()
        self.btn_cancel=QPushButton("Cancel"); self.btn_retry=QPushButton("Retry")
        act.addWidget(self.btn_cancel); act.addWidget(self.btn_retry); left.addLayout(act)
        self.details=QTextEdit(); self.details.setReadOnly(True)
        right.addWidget(QLabel("Run details / Logs (tail)")); right.addWidget(self.details,1)
        spl=QSplitter(); lw=QWidget(); rw=QWidget(); lw.setLayout(left); rw.setLayout(right); spl.addWidget(lw); spl.addWidget(rw)
        h.addWidget(spl)

        self.table.itemSelectionChanged.connect(self.refresh_details)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_retry.clicked.connect(self.retry_selected)

        # Jobs view polling reduced to 3s to lower API traffic
        self.t=QTimer(self); self.t.timeout.connect(self.refresh); self.t.start(3000); self.refresh()

    def refresh(self):
        def fn():
            return requests.get(f"{API}/jobs",timeout=5, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                res.raise_for_status(); rows=res.json()
                self.table.setRowCount(len(rows))
                for i,row in enumerate(rows):
                    for j,k in enumerate(["id","device","type","status","created_at"]):
                        self.table.setItem(i,j,QTableWidgetItem(str(row.get(k,""))))
            except Exception:
                pass
        self._async_call(fn, done)

    def _sel_job_id(self):
        it = self.table.currentItem()
        if not it: return None
        row = it.row()
        jid_item = self.table.item(row, 0)
        return int(jid_item.text()) if jid_item else None

    def refresh_details(self):
        jid = self._sel_job_id()
        if not jid: return
        def fn_runs():
            return requests.get(f"{API}/runs", params={"job_id": jid}, timeout=10)
        def fn_logs():
            return requests.get(f"{API}/jobs/{jid}/logs", params={"lines":200}, timeout=10)
        def done_runs(res_runs):
            try:
                runs_json = res_runs.json() if (not isinstance(res_runs, Exception) and res_runs.status_code==200) else []
            except Exception:
                runs_json = []
            def done_logs(res_logs):
                try:
                    text = res_logs.text if (not isinstance(res_logs, Exception) and res_logs.status_code==200) else ""
                except Exception:
                    text = ""
                self.details.setPlainText(json.dumps(runs_json, indent=2) + "\n\n" + text)
            self._async_call(fn_logs, done_logs)
        self._async_call(fn_runs, done_runs)

    def cancel_selected(self):
        jid = self._sel_job_id()
        if not jid:
            return
        def fn():
            return requests.post(f"{API}/jobs/{jid}/cancel", headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                if res.status_code!=200: QMessageBox.warning(self,"Cancel",f"{res.status_code}: {res.text}")
            except Exception as e:
                QMessageBox.warning(self, "Cancel", str(e))
        self._async_call(fn, done)

    def retry_selected(self):
        jid = self._sel_job_id()
        if not jid:
            return
        def fn():
            return requests.post(f"{API}/jobs/{jid}/retry", headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                if res.status_code!=200: QMessageBox.warning(self,"Retry",f"{res.status_code}: {res.text}")
            except Exception as e:
                QMessageBox.warning(self, "Retry", str(e))
        self._async_call(fn, done)

class CycleBuilderPage(QWidget):
    """Minimal, functional Cycle Builder with Palette · Canvas · Inspector.
    Saves/loads under artifacts/cycles; Run Now enqueues current canvas.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_step = None
        self.steps = []  # list[dict]
        root = QVBoxLayout(self)

        # Header actions
        hdr = QHBoxLayout()
        self.btn_new = QPushButton("New")
        self.btn_load = QPushButton("Load…")
        self.btn_save = QPushButton("Save…")
        self.btn_est = QPushButton("Estimate")
        self.btn_run = QPushButton("Run Now")
        self.auto_scrcpy = QCheckBox("Auto-open scrcpy")
        for w in (self.btn_new, self.btn_load, self.btn_save, self.btn_est, self.btn_run, self.auto_scrcpy): hdr.addWidget(w)
        hdr.addStretch(1)
        root.addLayout(hdr)

        # Three panes: Palette | Canvas | Inspector
        panes = QHBoxLayout()
        # Palette
        self.palette = QListWidget(); self.palette.setFixedWidth(220)
        palette_items = [
            ("warmup", "Warmup {seconds, like_prob}"),
            ("post_video", "Post video {video, caption}"),
            ("break", "Break {duration}"),
            ("rotate_identity", "Rotate identity (soft)"),
        ]
        for key, desc in palette_items:
            it = QListWidgetItem(f"{key} — {desc}")
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.palette.addItem(it)
        # Canvas
        self.canvas = QListWidget(); self.canvas.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.canvas.setDragEnabled(True); self.canvas.setAcceptDrops(True); self.canvas.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        # Inspector
        self.inspect_box = QGroupBox("Properties")
        self.inspect_form = QFormLayout(self.inspect_box)

        panes.addWidget(self.palette)
        panes.addWidget(self.canvas, 1)
        panes.addWidget(self.inspect_box)
        root.addLayout(panes, 1)

        # Footer: live preview
        self.preview = QTextEdit(); self.preview.setReadOnly(True); self.preview.setMinimumHeight(110)
        root.addWidget(self.preview)

        # Signals
        self.palette.itemDoubleClicked.connect(self.add_from_palette)
        self.canvas.currentItemChanged.connect(self.on_canvas_selection)
        self.btn_new.clicked.connect(self.new_cycle)
        self.btn_load.clicked.connect(self.load_cycle_dialog)
        self.btn_save.clicked.connect(self.save_cycle_dialog)
        self.btn_est.clicked.connect(self.estimate)
        self.btn_run.clicked.connect(self.run_now)

        self.new_cycle()

    # --------- model helpers ---------
    def _default_for(self, t: str) -> dict:
        if t == "warmup": return {"type":"warmup","duration":120,"like_prob":0.07}
        if t == "post_video": return {"type":"post_video","video":"","caption":""}
        if t == "break": return {"type":"break","duration":60}
        if t == "rotate_identity": return {"type":"rotate_identity","soft":True}
        return {"type":t}

    def _refresh_preview(self):
        lines=[]
        for i, st in enumerate(self.steps):
            t = st.get("type")
            if t=="warmup": lines.append(f"{i+1}. warmup {st.get('duration',0)}s like={st.get('like_prob',0)}")
            elif t=="break": lines.append(f"{i+1}. break {st.get('duration',0)}s")
            elif t=="post_video": lines.append(f"{i+1}. post_video {os.path.basename(st.get('video',''))}")
            else: lines.append(f"{i+1}. {t}")
        self.preview.setPlainText("\n".join(lines))

    def _sync_canvas(self):
        self.canvas.blockSignals(True)
        self.canvas.clear()
        for st in self.steps:
            t=st.get("type","?")
            text = t
            if t=="warmup": text = f"warmup · {st.get('duration',0)}s"
            if t=="break": text = f"break · {st.get('duration',0)}s"
            if t=="post_video": text = f"post_video · {os.path.basename(st.get('video',''))}"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, st)
            self.canvas.addItem(it)
        self.canvas.blockSignals(False)
        self._refresh_preview()

    def _rebind_inspector(self, st: dict | None):
        # Clear form
        while self.inspect_form.rowCount():
            self.inspect_form.removeRow(0)
        if not st:
            return
        t = st.get("type")
        # Common: type label
        self.inspect_form.addRow(QLabel(f"Type: {t}"))
        # Build fields by type
        if t == "warmup":
            s = QSpinBox(); s.setRange(10, 7200); s.setValue(int(st.get("duration",120)))
            p = QLineEdit(str(st.get("like_prob",0.07)))
            self.inspect_form.addRow("Seconds", s); self.inspect_form.addRow("Like prob (0-1)", p)
            def on_change():
                try: st["duration"] = int(s.value())
                except Exception: pass
                try: st["like_prob"] = float(p.text() or 0)
                except Exception: st["like_prob"]=0.07
                self._sync_canvas()
            s.valueChanged.connect(lambda _: on_change()); p.textChanged.connect(lambda _: on_change())
        elif t == "post_video":
            path = QLineEdit(st.get("video","")); cap = QLineEdit(st.get("caption",""))
            btn = QPushButton("Browse…")
            def pick():
                fn,_=QFileDialog.getOpenFileName(self,"Pick video","","Video files (*.mp4 *.mov *.mkv)")
                if fn: path.setText(fn)
            btn.clicked.connect(pick)
            def on_change():
                st["video"]=path.text().strip(); st["caption"]=cap.text()
                self._sync_canvas()
            path.textChanged.connect(lambda _: on_change()); cap.textChanged.connect(lambda _: on_change())
            row=QHBoxLayout(); row.addWidget(path); row.addWidget(btn); wrap=QWidget(); wrap.setLayout(row)
            self.inspect_form.addRow("Video", wrap); self.inspect_form.addRow("Caption", cap)
        elif t == "break":
            s = QSpinBox(); s.setRange(5, 24*3600); s.setValue(int(st.get("duration",60)))
            self.inspect_form.addRow("Duration (s)", s)
            def on_change():
                st["duration"] = int(s.value()); self._sync_canvas()
            s.valueChanged.connect(lambda _: on_change())
        elif t == "rotate_identity":
            soft = QCheckBox("Soft (no reboot)"); soft.setChecked(bool(st.get("soft",True)))
            def on_change():
                st["soft"]=soft.isChecked(); self._sync_canvas()
            soft.toggled.connect(lambda _: on_change())
            self.inspect_form.addRow("Mode", soft)

    # --------- actions ---------
    def add_from_palette(self, item: QListWidgetItem):
        t = item.data(Qt.ItemDataRole.UserRole)
        self.steps.append(self._default_for(t))
        self._sync_canvas()

    def on_canvas_selection(self, cur: QListWidgetItem, _prev: QListWidgetItem):
        st = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        self.current_step = st
        self._rebind_inspector(st)

    def new_cycle(self):
        self.steps = []
        self._sync_canvas(); self._rebind_inspector(None)

    def save_cycle_dialog(self):
        pathlib.Path("artifacts/cycles").mkdir(parents=True, exist_ok=True)
        fn,_=QFileDialog.getSaveFileName(self, "Save cycle as", "artifacts/cycles/cycle.json", "JSON (*.json)")
        if not fn: return
        try:
            with open(fn, "w", encoding="utf-8") as f:
                json.dump({"schema":"ta.cycle.v1","name":pathlib.Path(fn).stem,"steps":self.steps}, f, indent=2)
            toast(self, f"Saved {fn}")
        except Exception as e:
            QMessageBox.warning(self, "Save", str(e))

    def load_cycle_dialog(self):
        fn,_=QFileDialog.getOpenFileName(self, "Load cycle", "artifacts/cycles", "JSON (*.json)")
        if not fn: return
        try:
            with open(fn, "r", encoding="utf-8") as f:
                data=json.load(f)
            self.steps = list(data.get("steps", []))
            self._sync_canvas(); self._rebind_inspector(None)
            toast(self, f"Loaded {fn}")
        except Exception as e:
            QMessageBox.warning(self, "Load", str(e))

    def estimate(self):
        # naive estimate
        total = 0; swipes=0; likes=0
        for st in self.steps:
            if st.get("type")=="warmup":
                dur=int(st.get("duration",120)); total+=dur
                swipes += max(1, int(dur/6))
                likes += int(swipes * float(st.get("like_prob",0.07)))
            elif st.get("type")=="break": total+=int(st.get("duration",60))
            elif st.get("type")=="post_video": total+=180
        QMessageBox.information(self, "Estimate", f"~{int(total/60)} min • ~{swipes} swipes • ~{likes} likes")

    def run_now(self):
        # Ask device
        serial, ok = QInputDialog.getText(self, "Run Now", "Device serial (empty = default):")
        if not ok: return
        serial = serial.strip()
        try:
            payload={"device_serial":serial, "steps":self.steps, "repeat":1, "sleep_between":[2,5]}
            r=requests.post(f"{API}/enqueue/pipeline", json=payload, timeout=10)
            if r.status_code==200: toast(self, f"Enqueued: {r.json()}")
            else: QMessageBox.warning(self, "Run Now", f"{r.status_code}: {r.text}")
        except Exception as e:
            QMessageBox.warning(self, "Run Now", str(e))

class LogsPage(QWidget):
    def _execute_pending_callback(self):
        if hasattr(self, '_pending_callback'):
            cb, res = self._pending_callback
            delattr(self, '_pending_callback')
            try:
                cb(res)
            except Exception as e:
                uilog(f"LogsPage._execute_pending_callback: callback raised {e!r}")

    def _async_call(self, fn, cb):
        import threading
        def _run():
            try:
                res = fn()
            except Exception as e:
                res = e
            self._pending_callback = (cb, res)
            QTimer.singleShot(0, self._execute_pending_callback)
        threading.Thread(target=_run, daemon=True).start()
    def __init__(self, parent=None):
        super().__init__(parent)
        v=QVBoxLayout(self)
        self.text=QTextEdit(); self.text.setReadOnly(True); v.addWidget(self.text,1)
        # Technical logs polling is gated by visibility; create timer but don't start here
        self.t=QTimer(self); self.t.timeout.connect(self.refresh)
        # Initial content will load on first showEvent

    def showEvent(self, event):  # type: ignore[override]
        try:
            if hasattr(self, 't'):
                self.refresh(); self.t.start(3000)
        except Exception:
            pass
        return super().showEvent(event)

    def hideEvent(self, event):  # type: ignore[override]
        try:
            if hasattr(self, 't'):
                self.t.stop()
        except Exception:
            pass
        return super().hideEvent(event)

    def refresh(self):
        def fn():
            return requests.get(f"{API}/logs",params={"source":"all","lines":800},timeout=5, headers=HEADERS)
        def done(res):
            try:
                if isinstance(res, Exception): raise res
                self.text.setPlainText(res.text if res.status_code==200 else res.text)
            except Exception as e:
                self.text.setPlainText(str(e))
        self._async_call(fn, done)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TikTok Automation — Max Client")
        self.resize(1280,800)
        central=QWidget(); self.setCentralWidget(central)
        h=QHBoxLayout(central)
        self.sidebar=QListWidget(); self.sidebar.setFixedWidth(210)
        for name in ["Overview","Quick Run","Cycle Builder","Pipelines","Schedules","Devices","Jobs/Runs","Logs","Settings"]:
            QListWidgetItem(name, self.sidebar)
        self.stack=QStackedWidget()
        h.addWidget(self.sidebar); h.addWidget(self.stack,1)
        self.page_over=OverviewPage(); self.page_quick=QuickRunPage(); self.page_cycles=CycleBuilderPage(); self.page_pipes=PipelinesPage(); self.page_sched=SchedulesPage(); self.page_dev=DevicesPage(); self.page_jobs=JobsPage(); self.page_logs=LogsPage()
        # Prepare settings page (will replace placeholder below)
        settings = QWidget(); sv = QVBoxLayout(settings)
        g = QGroupBox("Human-like Behavior Settings"); gl = QFormLayout(g)
        self.like_prob_spin = QDoubleSpinBox(); self.like_prob_spin.setRange(0.0,1.0); self.like_prob_spin.setSingleStep(0.01)
        self.watch_lo = QSpinBox(); self.watch_lo.setRange(1,120)
        self.watch_hi = QSpinBox(); self.watch_hi.setRange(2,180)
        self.enable_share = QCheckBox("Tap Share occasionally (no posting)")
        self.enable_bookmark = QCheckBox("Randomly add to bookmarks")
        self.enable_volume = QCheckBox("Randomize volume during sessions")
        gl.addRow("Like probability", self.like_prob_spin)
        gl.addRow("Watch seconds (lo)", self.watch_lo)
        gl.addRow("Watch seconds (hi)", self.watch_hi)
        gl.addRow(self.enable_share)
        gl.addRow(self.enable_bookmark)
        gl.addRow(self.enable_volume)
        sv.addWidget(g)
        btns = QHBoxLayout(); self.btn_settings_reload=QPushButton("Reload"); self.btn_settings_save=QPushButton("Save"); btns.addStretch(1); btns.addWidget(self.btn_settings_reload); btns.addWidget(self.btn_settings_save)
        sv.addLayout(btns)

        def load_settings():
            try:
                r = requests.get(f"{API}/config", timeout=5, headers=HEADERS)
                if r.status_code==200:
                    c=r.json(); s=c.get("safety",{})
                    self.like_prob_spin.setValue(float(s.get("like_probability",0.07)))
                    self.watch_lo.setValue(int(s.get("watch_lo",6)))
                    self.watch_hi.setValue(int(s.get("watch_hi",13)))
                    # feature flags default off
                    f=c.get("features",{})
                    self.enable_share.setChecked(bool(f.get("share_tap", False)))
                    self.enable_bookmark.setChecked(bool(f.get("bookmark_random", False)))
                    self.enable_volume.setChecked(bool(f.get("volume_random", False)))
            except Exception as e:
                uilog(f"Settings reload failed: {e}")
        def save_settings():
            try:
                r = requests.get(f"{API}/config", timeout=5, headers=HEADERS)
                c = r.json() if r.status_code==200 else {}
                c.setdefault("safety",{})
                c["safety"]["like_probability"]=float(self.like_prob_spin.value())
                c["safety"]["watch_lo"]=int(self.watch_lo.value())
                c["safety"]["watch_hi"]=int(self.watch_hi.value())
                c.setdefault("features",{})
                c["features"]["share_tap"]=bool(self.enable_share.isChecked())
                c["features"]["bookmark_random"]=bool(self.enable_bookmark.isChecked())
                c["features"]["volume_random"]=bool(self.enable_volume.isChecked())
                pr = requests.post(f"{API}/config", json=c, timeout=8, headers=HEADERS)
                if pr.status_code==200:
                    toast(self, "Settings saved")
                else:
                    QMessageBox.warning(self, "Settings", f"Save failed: {pr.status_code}")
            except Exception as e:
                QMessageBox.warning(self, "Settings", str(e))
        self.btn_settings_reload.clicked.connect(load_settings)
        self.btn_settings_save.clicked.connect(save_settings)
        load_settings()
        # Replace old placeholder
        self.page_settings = settings
        self.stack.removeWidget(self.stack.widget(8))
        self.stack.addWidget(self.page_settings)

        # Add pages in sidebar order
        for w in [self.page_over,self.page_quick,self.page_cycles,self.page_pipes,self.page_sched,self.page_dev,self.page_jobs,self.page_logs,self.page_settings]:
            self.stack.addWidget(w)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        # menu / shortcuts
        self._setup_menu()
        # theme
        qss = load_qss()
        if qss: self.setStyleSheet(qss)

    def _setup_menu(self):
        bar = self.menuBar()
        filem = bar.addMenu("&File")
        act_quit = QAction("Quit", self); act_quit.triggered.connect(self.close); filem.addAction(act_quit)
        viewm = bar.addMenu("&View")
        act_scrcpy = QAction("Open scrcpy", self); act_scrcpy.triggered.connect(self.page_over.open_scrcpy_clicked); viewm.addAction(act_scrcpy)
        # shortcuts
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.page_over.toggle_warmup)


def run_gui():
    app=QApplication(sys.argv); win=MainWindow(); win.show(); app.exec()

if __name__=="__main__": run_gui()
