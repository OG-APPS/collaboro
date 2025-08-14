from __future__ import annotations
import os, sys, time, requests, subprocess, contextlib, socket, atexit

def find_free_port(preferred: int = 8000) -> int:
    base: int = int(preferred)
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        if s.connect_ex(("127.0.0.1", base)) != 0:
            return base
    for p in range(base + 1, base + 50):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s2:
            if s2.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return base + 60

def cleanup_all(procs: list[subprocess.Popen]):
    for p in procs:
        try:
            if p and p.poll() is None:
                p.terminate()
        except Exception:
            pass

def main():
    host="127.0.0.1"; py=sys.executable
    api_port=find_free_port(8000)
    env=os.environ.copy(); env["API_URL"]=f"http://{host}:{api_port}"
    # If API_TOKEN is set in outer environment, children will pick it up
    token = env.get("API_TOKEN", "").strip()
    api_cmd=[py,"-m","uvicorn","orchestrator.api:app","--host",host,"--port",str(api_port),"--log-level","info"]
    api = subprocess.Popen(api_cmd, env=env)
    # Wait for API up
    for _ in range(120):
        try:
            headers = ({"X-API-Token": token} if token else {})
            r = requests.get(f"http://{host}:{api_port}/health", timeout=1.0, headers=headers)
            if r.status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.25)
    # auto-detect device
    dev=env.get("DEVICE_SERIAL","")
    if not dev:
        try:
            out = subprocess.check_output(["adb","devices"], text=True)
            for line in out.splitlines():
                if line.strip().endswith("device") and not line.startswith("List of devices"):
                    dev = line.split("\t")[0].strip(); break
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    env["DEVICE_SERIAL"]=dev or ""
    # start scheduler + worker + GUI
    scheduler=subprocess.Popen([py,"-m","orchestrator.scheduler"], env=env)
    worker=subprocess.Popen([py,"-m","worker.device_worker"], env=env)
    gui=subprocess.Popen([py,"-m","ui.app"], env=env)
    print(f"[up] running on API http://{host}:{api_port}. Press Ctrl+C to stop.")
    try:
        while True:
            # Poll GUI until it exits; allows Ctrl+C to trigger KeyboardInterrupt
            rc = gui.poll()
            if rc is not None:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[down] shutting down...")
    finally:
        cleanup_all([worker, scheduler, api, gui])
        # Ensure cleanup on interpreter exit as an extra guard
        atexit.register(lambda: cleanup_all([worker, scheduler, api, gui]))

if __name__=="__main__": main()
