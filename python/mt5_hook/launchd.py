from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from . import paths

LABEL = "com.gasiimwe.mt5-demo-manager"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist_body() -> str:
    root = paths.project_root()
    python = sys.executable
    log = paths.manager_log_path()
    err = paths.logs_dir() / "manager.err.log"
    return dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>{LABEL}</string>
          <key>WorkingDirectory</key>
          <string>{root}</string>
          <key>EnvironmentVariables</key>
          <dict>
            <key>PYTHONPATH</key>
            <string>{root / "python"}</string>
            <key>MT5_MANAGER_CAFFEINATED</key>
            <string>1</string>
            <key>PYTHONUNBUFFERED</key>
            <string>1</string>
          </dict>
          <key>ProgramArguments</key>
          <array>
            <string>/usr/bin/caffeinate</string>
            <string>-i</string>
            <string>{python}</string>
            <string>-u</string>
            <string>-m</string>
            <string>mt5_hook</string>
            <string>manage</string>
            <string>--interval</string>
            <string>20</string>
          </array>
          <key>RunAtLoad</key>
          <true/>
          <key>KeepAlive</key>
          <true/>
          <key>StandardOutPath</key>
          <string>{log}</string>
          <key>StandardErrorPath</key>
          <string>{err}</string>
        </dict>
        </plist>
        """
    )


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_body(), encoding="utf-8")
    domain = _gui_domain()
    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False, capture_output=True)
    loaded = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    if loaded.returncode != 0:
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=False)
    print(f"Installed background manager: {path}")
    print("Keep MetaTrader 5 open with PythonBridgeEA attached and Algo Trading on.")
    print("Keep this Mac awake (or plugged in). Laptop sleep still pauses trading.")


def uninstall() -> None:
    path = plist_path()
    domain = _gui_domain()
    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False)
    subprocess.run(["launchctl", "unload", str(path)], check=False)
    if path.exists():
        path.unlink()
    print("Removed background manager.")
