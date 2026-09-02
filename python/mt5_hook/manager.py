"""Retired 24/7 Python manager. PythonBridgeEA AutoTrade owns entries and exits."""
from __future__ import annotations


def run(interval: int = 20, prevent_sleep: bool = False, caffeinate: bool = False) -> None:
    _ = interval, prevent_sleep, caffeinate
    print("Python manager is retired. PythonBridgeEA AutoTrade=true handles entries and exits.")
    print("Use hook.cmd daemon-uninstall if the old Task Scheduler job is still running.")
