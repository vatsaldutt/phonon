#!/usr/bin/env python3
"""
start.py — Single command to launch everything.

    python3 start.py

Starts in order:
  1. Home Assistant Core  (hass binary directly from venv — no shell activation needed)
  2. CRYSTAL API server   (uvicorn on port 8000)
  3. Cloudflare Tunnel    (cloudflared tunnel run crystal-music-api)

Ctrl+C shuts all three down cleanly.

Required env vars (add to ~/.bashrc so you don't re-export every time):
    HA_TOKEN      — Home Assistant long-lived access token
    FAN_ENTITY    — e.g. fan.ceiling_fan
    LIGHT_ENTITY  — e.g. light.ceiling_fan_light
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, LASTFM_API_KEY  (music backend)

    API_MODULE    — IMPORTANT: set this to yourfilename:app
                    e.g. if your backend is server.py  → API_MODULE=server:app
                         if your backend is backend.py → API_MODULE=backend:app
                    Default: main:app

Optional:
    API_HOST      — bind address for uvicorn  (default: 0.0.0.0)
    API_PORT      — port for uvicorn          (default: 8000)
    HA_VENV       — path to HA virtualenv     (default: ~/homeassistant)
    HASS_DELAY_S  — seconds to wait for HA before starting tunnel (default: 8)
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

API_HOST   = os.environ.get("API_HOST", "0.0.0.0")
API_PORT   = os.environ.get("API_PORT", "8000")
API_MODULE = os.environ.get("API_MODULE", "server:app")
HA_VENV    = Path(os.environ.get("HA_VENV", Path.home() / "homeassistant")).expanduser()
HASS_DELAY = int(os.environ.get("HASS_DELAY_S", "8"))

# ── ANSI colour helpers ────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
COLORS = {
    "hass":        "\033[94m",   # blue
    "api":         "\033[92m",   # green
    "cloudflared": "\033[95m",   # magenta
    "launcher":    "\033[93m",   # yellow
}


def tag(name: str, text: str) -> str:
    c = COLORS.get(name, "")
    return f"{c}{BOLD}[{name}]{RESET} {text}"


def log(name: str, text: str):
    for line in text.rstrip("\n").splitlines():
        print(tag(name, line), flush=True)


# ── Process wrapper ────────────────────────────────────────────────────────────

class ManagedProcess:
    def __init__(self, name: str, cmd: list[str], env=None):
        self.name  = name
        self.cmd   = cmd
        self.env   = env
        self.proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    def start(self):
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=self.env,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        log("launcher", f"Started {self.name} (PID {self.proc.pid})")

    def _read(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            log(self.name, line)
        rc = self.proc.wait()
        log("launcher", f"{self.name} exited (code {rc})")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            log("launcher", f"Stopping {self.name}…")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


# ── Pre-flight checks ──────────────────────────────────────────────────────────

def preflight():
    ok = True

    # Check hass binary exists in venv
    hass_bin = HA_VENV / "bin" / "hass"
    if not hass_bin.exists():
        log("launcher", f"ERROR: hass not found at {hass_bin}")
        log("launcher", f"       Create the venv and install hass:")
        log("launcher", f"       python3 -m venv {HA_VENV}")
        log("launcher", f"       {HA_VENV}/bin/pip install homeassistant")
        ok = False
    else:
        log("launcher", f"hass binary: {hass_bin} ✓")

    # Check API module file exists
    module_name = API_MODULE.split(":")[0]
    module_file = Path(module_name + ".py")
    if not module_file.exists():
        log("launcher", f"ERROR: Cannot find '{module_file}' in current directory.")
        log("launcher", f"       Set API_MODULE to your backend filename:")
        log("launcher", f"       e.g.  export API_MODULE=server:app")
        log("launcher", f"       e.g.  export API_MODULE=backend:app")
        log("launcher", f"       Available .py files here:")
        for f in sorted(Path(".").glob("*.py")):
            log("launcher", f"         {f}")
        ok = False
    else:
        log("launcher", f"API module:  {module_file} ({API_MODULE}) ✓")

    # Check cloudflared exists
    import shutil
    if not shutil.which("cloudflared"):
        log("launcher", "WARNING: cloudflared not found in PATH — tunnel won't start")
        log("launcher", "         Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
    else:
        log("launcher", "cloudflared: found ✓")

    # Warn about missing fan env vars
    for var in ("HA_TOKEN", "FAN_ENTITY", "LIGHT_ENTITY"):
        if not os.environ.get(var):
            log("launcher", f"WARNING: {var} not set — fan endpoints will return 503")

    return ok


# ── Build process list ─────────────────────────────────────────────────────────

def build_processes() -> list[ManagedProcess]:
    # ── 1. Home Assistant Core ─────────────────────────────────────────────────
    # Call the hass binary directly — no shell, no `source` needed.
    # The venv binary already has the correct sys.path baked into its shebang.
    hass_bin = str(HA_VENV / "bin" / "hass")
    hass = ManagedProcess("hass", [hass_bin])

    # ── 2. CRYSTAL API (uvicorn) ───────────────────────────────────────────────
    api = ManagedProcess(
        "api",
        [sys.executable, "-m", "uvicorn", API_MODULE,
         "--host", API_HOST, "--port", API_PORT, "--reload"],
    )

    # ── 3. Cloudflare Tunnel ───────────────────────────────────────────────────
    tunnel = ManagedProcess(
        "cloudflared",
        ["cloudflared", "tunnel", "run", "crystal-music-api"],
    )

    return [hass, api, tunnel]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{COLORS['launcher']}CRYSTAL + Fan API Launcher{RESET}\n")

    if not preflight():
        print(f"\n{BOLD}Fix the errors above then re-run.{RESET}\n")
        sys.exit(1)

    print()
    procs = build_processes()
    running: list[ManagedProcess] = []

    def shutdown(sig=None, frame=None):
        print(f"\n{BOLD}Shutting down…{RESET}")
        for p in reversed(running):
            p.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start hass first, give it time to initialize before the tunnel opens
    hass = procs[0]
    hass.start()
    running.append(hass)
    log("launcher", f"Waiting {HASS_DELAY}s for Home Assistant to initialise…")
    time.sleep(HASS_DELAY)

    # Start API and tunnel
    for p in procs[1:]:
        p.start()
        running.append(p)

    log("launcher", f"All services started.")
    log("launcher", f"  API local:  http://localhost:{API_PORT}")
    log("launcher", f"  API public: https://api.vatsaldutt.com")
    log("launcher", f"  API docs:   http://localhost:{API_PORT}/docs")
    log("launcher", "Press Ctrl+C to stop everything.\n")

    # Watch loop: restart crashed processes
    while True:
        time.sleep(5)
        for p in running:
            if not p.is_running():
                log("launcher", f"{p.name} crashed — restarting in 3s…")
                time.sleep(3)
                p.start()


if __name__ == "__main__":
    main()