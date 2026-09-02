from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from xml.sax.saxutils import escape

from . import paths

TASK_NAME = "mt5-demo-manager"
MAC_LABEL = "com.mt5-demo-bot.manager"


def install() -> None:
    kind = paths.host_os()
    print(f"Detected OS: {kind}")
    print("Python manager is retired. PythonBridgeEA AutoTrade=true runs entries and exits inside MT5.")
    print("Not installing a background Python task. Use hook.cmd daemon-uninstall to remove an old one.")


def uninstall() -> None:
    kind = paths.host_os()
    print(f"Detected OS: {kind}")
    if kind == "windows":
        _uninstall_windows()
        return
    if kind == "macos":
        _uninstall_macos()
        return
    print("No background manager on this OS.")


def task_xml_path() -> Path:
    return paths.logs_dir() / "mt5-demo-manager.task.xml"


def _python() -> str:
    return sys.executable


def _install_windows() -> None:
    root = paths.project_root()
    runner = root / "run_manager.cmd"
    if not runner.is_file():
        raise RuntimeError(f"Missing {runner}")
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    user_id = f"{domain}\\{user}" if domain and user else user
    xml = dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <RegistrationInfo>
            <Description>MT5 demo manager - ticks the Python hook while you are logged in.</Description>
          </RegistrationInfo>
          <Triggers>
            <LogonTrigger>
              <Enabled>true</Enabled>
              <UserId>{escape(user_id)}</UserId>
            </LogonTrigger>
          </Triggers>
          <Principals>
            <Principal id="Author">
              <UserId>{escape(user_id)}</UserId>
              <LogonType>InteractiveToken</LogonType>
              <RunLevel>LeastPrivilege</RunLevel>
            </Principal>
          </Principals>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <AllowHardTerminate>true</AllowHardTerminate>
            <StartWhenAvailable>true</StartWhenAvailable>
            <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
            <IdleSettings>
              <StopOnIdleEnd>false</StopOnIdleEnd>
              <RestartOnIdle>false</RestartOnIdle>
            </IdleSettings>
            <AllowStartOnDemand>true</AllowStartOnDemand>
            <Enabled>true</Enabled>
            <Hidden>true</Hidden>
            <RunOnlyIfIdle>false</RunOnlyIfIdle>
            <WakeToRun>false</WakeToRun>
            <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
            <Priority>7</Priority>
            <RestartOnFailure>
              <Interval>PT1M</Interval>
              <Count>999</Count>
            </RestartOnFailure>
          </Settings>
          <Actions Context="Author">
            <Exec>
              <Command>{escape(str(runner))}</Command>
              <WorkingDirectory>{escape(str(root))}</WorkingDirectory>
            </Exec>
          </Actions>
        </Task>
        """
    )
    xml_path = task_xml_path()
    xml_path.write_text(xml, encoding="utf-16")
    created = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        raise RuntimeError(
            ((created.stdout or "") + (created.stderr or "")).strip() or "schtasks /Create failed"
        )
    subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], check=False, capture_output=True)
    print(f"Installed background manager: Task Scheduler -> {TASK_NAME}")
    print("Keep MetaTrader 5 open with PythonBridgeEA attached and Algo Trading on.")
    print("Keep this PC awake (or plugged in). Sleep still pauses trading.")
    print(f"Logs: {paths.manager_log_path()}")


def _uninstall_windows() -> None:
    subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], check=False, capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False, capture_output=True)
    xml_path = task_xml_path()
    if xml_path.exists():
        xml_path.unlink()
    print("Removed background manager.")


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _plist_body() -> str:
    root = paths.project_root()
    python = _python()
    log = paths.manager_log_path()
    err = paths.manager_err_log_path()
    return dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>{MAC_LABEL}</string>
          <key>WorkingDirectory</key>
          <string>{root}</string>
          <key>EnvironmentVariables</key>
          <dict>
            <key>PYTHONPATH</key>
            <string>{paths.python_dir()}</string>
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
            <string>--no-sleep</string>
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


def _install_macos() -> None:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_plist_body(), encoding="utf-8")
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False, capture_output=True)
    loaded = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    if loaded.returncode != 0:
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{MAC_LABEL}"], check=False)
    print(f"Installed background manager: {path}")
    print("Keep MetaTrader 5 open with PythonBridgeEA attached and Algo Trading on.")
    print("Keep this Mac awake (or plugged in). Laptop sleep still pauses trading.")


def _uninstall_macos() -> None:
    domain = f"gui/{os.getuid()}"
    labels = [MAC_LABEL, "com.gasiimwe.mt5-demo-manager"]
    for label in labels:
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        subprocess.run(["launchctl", "bootout", domain, str(plist)], check=False)
        subprocess.run(["launchctl", "unload", str(plist)], check=False)
        if plist.exists():
            plist.unlink()
    print("Removed background manager.")
