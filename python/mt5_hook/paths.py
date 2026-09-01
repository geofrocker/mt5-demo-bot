from __future__ import annotations

import os
from pathlib import Path

DEFAULT_TOKEN = "demo-local-hook"
HTTP_PORT = 18790
TCP_PORT = 18789


def token() -> str:
    return os.environ.get("MT5_HOOK_TOKEN", DEFAULT_TOKEN)


def _wine_common_files() -> list[Path]:
    support = Path.home() / "Library" / "Application Support"
    found: list[Path] = []
    for prefix in support.glob("net.metaquotes.wine.metatrader*"):
        found.extend(prefix.glob("drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"))
    return found


def common_files_dir() -> Path:
    wine = _wine_common_files()
    native_mac = (
        Path.home()
        / "Library"
        / "Application Support"
        / "MetaQuotes"
        / "Terminal"
        / "Common"
        / "Files"
    )
    appdata = os.environ.get("APPDATA", "")
    win = Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" if appdata else None
    candidates = [*wine, native_mac]
    if win is not None:
        candidates.append(win)
    for path in candidates:
        if path.exists():
            return path
    return wine[0] if wine else native_mac


def hook_stem(symbol: str | None = None) -> str:
    if not symbol:
        return "mt5_hook"
    s = symbol.upper().replace(".", "_").replace("#", "_").replace("/", "")
    return f"mt5_hook_{s}"


def cmd_path(symbol: str | None = None) -> Path:
    return common_files_dir() / f"{hook_stem(symbol)}_cmd.json"


def result_path(symbol: str | None = None) -> Path:
    return common_files_dir() / f"{hook_stem(symbol)}_result.json"


def snapshot_path(symbol: str | None = None) -> Path:
    return common_files_dir() / f"{hook_stem(symbol)}_snapshot.json"


def enabled_config_path() -> Path:
    return project_root() / "config" / "enabled.json"


def list_snapshot_symbols() -> list[str]:
    common = common_files_dir()
    if not common.exists():
        return []
    found: list[str] = []
    for path in sorted(common.glob("mt5_hook_*_snapshot.json")):
        name = path.name
        if name == "mt5_hook_snapshot.json":
            continue
        stem = name.removeprefix("mt5_hook_").removesuffix("_snapshot.json")
        if stem:
            found.append(stem.replace("_", "."))
    return found


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def logs_dir() -> Path:
    path = project_root() / "logs"
    path.mkdir(exist_ok=True)
    return path


def manager_state_path() -> Path:
    return logs_dir() / "manager_state.json"


def manager_log_path() -> Path:
    return logs_dir() / "manager.log"
