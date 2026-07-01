#!/usr/bin/env python3
"""
start.py — Single command to launch everything.

    python3 start.py

Starts in order:
  1. Home Assistant Core  (hass, background, your ~/homeassistant venv)
  2. CRYSTAL API server   (uvicorn on port 8000)
  3. Cloudflare Tunnel    (cloudflared tunnel run crystal-music-api)

Ctrl+C shuts all three down cleanly.

Required env vars (add to ~/.zshrc so you don't re-export every time):
    HA_TOKEN      — Home Assistant long-lived access token
    FAN_ENTITY    — e.g. fan.ceiling_fan
    LIGHT_ENTITY  — e.g. light.ceiling_fan_light
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, LASTFM_API_KEY  (music backend)

Optional:
    API_HOST      — bind address for uvicorn  (default: 0.0.0.0)
    API_PORT      — port for uvicorn          (default: 8000)
    API_MODULE    — module:app string         (default: main:app)
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

# ── Config ────────────────────────────────────────────────────────────────────

API_HOST   = os.environ.get("API_HOST", "0.0.0.0")
API_PORT   = os.environ.get("API_PORT", "8000")
API_MODULE = os.environ.get("API_MODULE", "main:app")
HA_VENV    = Path(os.environ.get("HA_VENV", Path.home() / "homeassistant"))
HASS_DELAY = int(os.environ.get("HASS_DELAY_S", "8"))

# ── ANSI colour helpers ───────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
COLORS = {
    "hass":       "\033[94m",   # blue
    "api":        "\033[92m",   # green
    "cloudflared":"\033[95m",   # magenta
    "launcher":   "\033[93m",   # yellow
}


def tag(name: str, text: str) -> str:
    c = COLORS.get(name, "")
    return f"{c}{BOLD}[{name}]{RESET} {text}"


def log(name: str, text: str):
    for line in text.rstrip("\n").splitlines():
        print(tag(name, line), flush=True)


# ── Process wrapper ───────────────────────────────────────────────────────────

class ManagedProcess:
    def __init__(self, name: str, cmd: list[str], shell: bool = False, env=None):
        self.name  = name
        self.cmd   = cmd
        self.shell = shell
        self.env   = env
        self.proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    def start(self):
        self.proc = subprocess.Popen(
            self.cmd,
            shell=self.shell,
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


# ── Build process list ────────────────────────────────────────────────────────

def build_processes() -> list[ManagedProcess]:
    procs: list[ManagedProcess] = []

    # ── 1. Home Assistant Core ────────────────────────────────────────────────
    # Needs its own virtualenv activated before calling hass.
    python = HA_VENV / "bin" / "python"
    if not python.exists():
        log("launcher", f"WARNING: HA venv not found at {HA_VENV}")
        log("launcher", "         Run: python3 -m venv ~/homeassistant && "
                        "source ~/homeassistant/bin/activate && pip install homeassistant")
    hass_cmd = f'source "{HA_VENV}/bin/activate" && hass'
    procs.append(ManagedProcess("hass", [hass_cmd], shell=True))

    # ── 2. CRYSTAL API (uvicorn) ──────────────────────────────────────────────
    procs.append(ManagedProcess(
        "api",
        [sys.executable, "-m", "uvicorn", API_MODULE,
         "--host", API_HOST, "--port", API_PORT, "--reload"],
    ))

    # ── 3. Cloudflare Tunnel ──────────────────────────────────────────────────
    procs.append(ManagedProcess(
        "cloudflared",
        ["cloudflared", "tunnel", "run", "crystal-music-api"],
    ))

    return procs


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{COLORS['launcher']}CRYSTAL + Fan API Launcher{RESET}\n")

    # Warn about missing required env vars (don't hard-stop; hass might supply them)
    for var in ("HA_TOKEN", "FAN_ENTITY", "LIGHT_ENTITY"):
        if not os.environ.get(var):
            log("launcher", f"WARNING: {var} is not set — fan endpoints will return 503")

    procs = build_processes()
    running: list[ManagedProcess] = []

    def shutdown(sig=None, frame=None):
        print(f"\n{BOLD}Shutting down…{RESET}")
        for p in reversed(running):
            p.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── Start hass first, give it time to bind before the tunnel opens ────────
    hass = procs[0]
    hass.start()
    running.append(hass)
    log("launcher", f"Waiting {HASS_DELAY}s for Home Assistant to start…")
    time.sleep(HASS_DELAY)

    # ── Start API and tunnel ──────────────────────────────────────────────────
    for p in procs[1:]:
        p.start()
        running.append(p)

    log("launcher", f"All services started. API → http://localhost:{API_PORT}")
    log("launcher", "Press Ctrl+C to stop everything.\n")

    # ── Watch loop: restart crashed processes ─────────────────────────────────
    while True:
        time.sleep(5)
        for p in running:
            if not p.is_running():
                log("launcher", f"{p.name} crashed — restarting in 3s…")
                time.sleep(3)
                p.start()


if __name__ == "__main__":
    main()