[CmdletBinding()] param([string]$PythonExe="python", [string]$DeviceSerial="")
$ErrorActionPreference="Stop"
function New-OkDir { param([string]$p) if(-not (Test-Path $p)){ New-Item -ItemType Directory -Path $p | Out-Null } }
Write-Host "==== Pre-checks ===="
$pyver = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if([version]$pyver -lt [version]"3.11"){ throw "Python 3.11+ required, found $pyver" }
Write-Host "`n==== Python environment ===="
if(-not (Test-Path ".venv")){ & $PythonExe -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "`n==== Folders & Config ===="
New-OkDir artifacts; New-OkDir artifacts\logs; New-OkDir artifacts\processed; New-OkDir artifacts\recordings
New-OkDir config; New-OkDir assets\icons

if(-not (Test-Path "config\config.yaml")){
@"
ui:
  theme: dark
  accent: "#7aa2f7"
  auto_open_scrcpy: false
  show_toasts: true
  keyboard_shortcuts: true
safety:
  warmup_seconds: 120
  like_probability: 0.07
  watch_lo: 6
  watch_hi: 13
system:
  api_port: 8000
  artifacts: "artifacts"
  default_device: ""
video:
  presets:
    repurpose_9x16: true
    normalize_audio: false
    watermark: ""
cycles:
  "Warmup + Post":
    steps:
      - {type: rotate_identity, soft: true}
      - {type: warmup, duration: 180}
      - {type: post_video, video: "", caption: ""}
schedules:
  "Daily":
    repeat: 1
    items:
      - {type: cycle, name: "Warmup + Post"}
      - {type: break, minutes: 60}
      - {type: cycle, name: "Warmup + Post"}
"@ | Out-File -Encoding utf8 config\config.yaml
}

Write-Host "`n==== Database init ===="
$initDb = @'
import sqlite3, os
os.makedirs("artifacts", exist_ok=True)
db = sqlite3.connect("artifacts/orchestrator.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, type TEXT, payload TEXT,
 status TEXT DEFAULT 'queued', created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS runs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, device TEXT, status TEXT,
 started_at DATETIME DEFAULT CURRENT_TIMESTAMP, ended_at DATETIME);
""")
db.commit()
print("Initialized DB at artifacts/orchestrator.db")
'@
$initDb | & .\.venv\Scripts\python.exe -

try{ $out=& adb devices; $line=($out -split "`r?`n" | Where-Object { $_ -match "device$" -and $_ -notmatch "List of devices" })[0]
 if($line){ $s=$line.Split("`t")[0]; if(-not $DeviceSerial){ $DeviceSerial=$s }; Write-Host "Auto-detected device serial: $s" }}
catch{}
if($DeviceSerial){ Write-Host "Using device serial: $DeviceSerial" } else { Write-Warning "No device detected." }
Write-Host "`n==== Done ===="
Write-Host "Run: .\\.venv\\Scripts\\Activate.ps1; python main.py"
