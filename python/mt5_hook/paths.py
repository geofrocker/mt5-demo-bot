from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TOKEN = "demo-local-hook"
HTTP_PORT = 18790
TCP_PORT = 18789

_SKIP_TERMINAL_DIRS = {"Common", "Community", "Help"}


def token() -> str:
    return os.environ.get("MT5_HOOK_TOKEN", DEFAULT_TOKEN)


def host_os() -> str:
    if os.name == "nt" or sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def is_windows() -> bool:
    return host_os() == "windows"


def is_macos() -> bool:
    return host_os() == "macos"


def hook_command() -> str:
    return "hook.cmd" if is_windows() else "./hook"


def daemon_backend() -> str:
    if is_windows():
        return "task_scheduler"
    if is_macos():
        return "launchd"
    return "none"


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


def manager_err_log_path() -> Path:
    return logs_dir() / "manager.err.log"


def enabled_config_path() -> Path:
    return project_root() / "config" / "enabled.json"


def python_dir() -> Path:
    return project_root() / "python"


@dataclass(frozen=True)
class Mt5Terminal:
    data_dir: Path
    experts_dir: Path
    install_dir: Path | None
    terminal_exe: Path | None
    metaeditor_exe: Path | None
    wine_prefix: Path | None = None
    wine_bin: Path | None = None

    @property
    def name(self) -> str:
        if self.install_dir is not None:
            return self.install_dir.name
        return self.data_dir.name


def _read_origin(path: Path) -> Path | None:
    if not path.exists():
        return None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "cp1252"):
        try:
            text = path.read_text(encoding=encoding).strip()
        except (OSError, UnicodeError):
            continue
        line = text.splitlines()[0].strip() if text else ""
        if line:
            return Path(line)
    return None


def wine_binary() -> Path | None:
    apps = Path("/Applications")
    if not apps.is_dir():
        return None
    for app in sorted(apps.glob("*MetaTrader*.app")):
        for name in ("wine64", "wine"):
            cand = app / "Contents" / "SharedSupport" / "wine" / "bin" / name
            if cand.is_file():
                return cand
    return None


def wine_prefixes() -> list[Path]:
    support = Path.home() / "Library" / "Application Support"
    if not support.is_dir():
        return []
    return sorted(support.glob("net.metaquotes.wine.metatrader*"))


def to_wine_windows_path(prefix: Path, posix_path: Path) -> str:
    drive_c = (prefix / "drive_c").resolve()
    try:
        rel = posix_path.resolve().relative_to(drive_c)
    except ValueError:
        return str(posix_path)
    return "C:\\" + str(rel).replace("/", "\\")


def _from_windows_path(prefix: Path, origin: Path) -> Path | None:
    raw = str(origin).replace("/", "\\")
    if len(raw) >= 3 and raw[1] == ":" and raw[2] == "\\":
        rest = raw[3:]
        mapped = prefix / "drive_c" / rest.replace("\\", "/")
        if mapped.exists():
            return mapped
    if origin.exists():
        return origin
    return None


def _exe(install: Path | None, name: str) -> Path | None:
    if install is None:
        return None
    cand = install / name
    return cand if cand.is_file() else None


def _terminal_from_data_dir(
    data_dir: Path,
    *,
    wine_prefix: Path | None = None,
    wine_bin: Path | None = None,
) -> Mt5Terminal | None:
    experts = data_dir / "MQL5" / "Experts"
    if not experts.is_dir():
        return None
    origin = _read_origin(data_dir / "origin.txt")
    install = origin
    if wine_prefix is not None and origin is not None:
        install = _from_windows_path(wine_prefix, origin) or origin
    terminal_exe = _exe(install, "terminal64.exe")
    metaeditor_exe = _exe(install, "metaeditor64.exe")
    if terminal_exe is None and install is not None and (install / "terminal.exe").is_file():
        return None
    return Mt5Terminal(
        data_dir=data_dir,
        experts_dir=experts,
        install_dir=install,
        terminal_exe=terminal_exe,
        metaeditor_exe=metaeditor_exe,
        wine_prefix=wine_prefix,
        wine_bin=wine_bin,
    )


def _terminal_from_install(
    install: Path,
    *,
    wine_prefix: Path | None = None,
    wine_bin: Path | None = None,
) -> Mt5Terminal | None:
    experts = install / "MQL5" / "Experts"
    if not experts.is_dir():
        return None
    terminal_exe = _exe(install, "terminal64.exe")
    if terminal_exe is None:
        return None
    return Mt5Terminal(
        data_dir=install,
        experts_dir=experts,
        install_dir=install,
        terminal_exe=terminal_exe,
        metaeditor_exe=_exe(install, "metaeditor64.exe"),
        wine_prefix=wine_prefix,
        wine_bin=wine_bin,
    )


def _windows_appdata_terminal_root() -> Path | None:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    return Path(appdata) / "MetaQuotes" / "Terminal"


def _scan_terminal_root(
    root: Path,
    *,
    wine_prefix: Path | None = None,
    wine_bin: Path | None = None,
) -> list[Mt5Terminal]:
    found: list[Mt5Terminal] = []
    if not root.is_dir():
        return found
    for child in root.iterdir():
        if not child.is_dir() or child.name in _SKIP_TERMINAL_DIRS:
            continue
        term = _terminal_from_data_dir(child, wine_prefix=wine_prefix, wine_bin=wine_bin)
        if term is not None:
            found.append(term)
    return found


def _windows_terminals() -> list[Mt5Terminal]:
    root = _windows_appdata_terminal_root()
    if root is None:
        return []
    found = _scan_terminal_root(root)
    found.sort(key=lambda t: t.data_dir.stat().st_mtime, reverse=True)
    return found


def _mac_terminals() -> list[Mt5Terminal]:
    found: list[Mt5Terminal] = []
    seen: set[Path] = set()
    wine_bin = wine_binary()

    native = Path.home() / "Library" / "Application Support" / "MetaQuotes" / "Terminal"
    for term in _scan_terminal_root(native):
        key = term.experts_dir.resolve()
        if key not in seen:
            seen.add(key)
            found.append(term)

    for prefix in wine_prefixes():
        for root in prefix.glob("drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal"):
            for term in _scan_terminal_root(root, wine_prefix=prefix, wine_bin=wine_bin):
                key = term.experts_dir.resolve()
                if key not in seen:
                    seen.add(key)
                    found.append(term)
        for base in (
            prefix / "drive_c" / "Program Files",
            prefix / "drive_c" / "Program Files (x86)",
        ):
            if not base.is_dir():
                continue
            for install in sorted(base.glob("*MetaTrader 5*")):
                term = _terminal_from_install(install, wine_prefix=prefix, wine_bin=wine_bin)
                if term is None:
                    continue
                key = term.experts_dir.resolve()
                if key not in seen:
                    seen.add(key)
                    found.append(term)

    found.sort(key=lambda t: t.data_dir.stat().st_mtime, reverse=True)
    return found


def list_mt5_terminals() -> list[Mt5Terminal]:
    if is_windows():
        return _windows_terminals()
    if is_macos():
        return _mac_terminals()
    return []


def primary_terminal() -> Mt5Terminal | None:
    terms = list_mt5_terminals()
    return terms[0] if terms else None


def common_files_dir() -> Path:
    if is_windows():
        root = _windows_appdata_terminal_root()
        if root is not None:
            return root / "Common" / "Files"
    wine: list[Path] = []
    for prefix in wine_prefixes():
        wine.extend(prefix.glob("drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"))
    native_mac = (
        Path.home()
        / "Library"
        / "Application Support"
        / "MetaQuotes"
        / "Terminal"
        / "Common"
        / "Files"
    )
    for path in [*wine, native_mac]:
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


def list_snapshot_symbols() -> list[str]:
    common = common_files_dir()
    if not common.exists():
        return []
    found: list[str] = []
    for path in sorted(common.glob("mt5_hook_*_snapshot.json")):
        name = path.name
        if name == "mt5_hook_snapshot.json":
            continue
        stem = name[len("mt5_hook_") : -len("_snapshot.json")]
        if stem:
            found.append(stem.replace("_", "."))
    return found


def describe() -> dict[str, object]:
    terms = list_mt5_terminals()
    primary = terms[0] if terms else None
    return {
        "os": host_os(),
        "daemon": daemon_backend(),
        "hook": hook_command(),
        "project_root": str(project_root()),
        "common_files": str(common_files_dir()),
        "common_files_exists": common_files_dir().exists(),
        "wine_prefix": None if not wine_prefixes() else str(wine_prefixes()[0]),
        "wine_bin": None if wine_binary() is None else str(wine_binary()),
        "primary_terminal": None
        if primary is None
        else {
            "name": primary.name,
            "data_dir": str(primary.data_dir),
            "experts_dir": str(primary.experts_dir),
            "install_dir": None if primary.install_dir is None else str(primary.install_dir),
            "terminal_exe": None if primary.terminal_exe is None else str(primary.terminal_exe),
            "metaeditor_exe": None if primary.metaeditor_exe is None else str(primary.metaeditor_exe),
            "wine": primary.wine_prefix is not None,
        },
        "terminals": [
            {
                "name": t.name,
                "data_dir": str(t.data_dir),
                "experts_dir": str(t.experts_dir),
                "wine": t.wine_prefix is not None,
            }
            for t in terms
        ],
    }
