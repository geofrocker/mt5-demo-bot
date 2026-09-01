from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import paths

EA_NAME = "PythonBridgeEA.mq5"


def ea_source() -> Path:
    return paths.project_root() / "MQL5" / "Experts" / EA_NAME


def _compile(term: paths.Mt5Terminal, mq5: Path) -> tuple[bool, str]:
    log_path = mq5.with_suffix(".log")
    try:
        if term.wine_bin and term.wine_prefix and term.install_dir is not None:
            env = os.environ.copy()
            env["WINEPREFIX"] = str(term.wine_prefix)
            win_file = paths.to_wine_windows_path(term.wine_prefix, mq5)
            proc = subprocess.run(
                [str(term.wine_bin), "metaeditor64.exe", f"/compile:{win_file}", "/log"],
                cwd=str(term.install_dir),
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
                env=env,
            )
        elif term.metaeditor_exe is not None:
            proc = subprocess.run(
                [str(term.metaeditor_exe), f"/compile:{mq5}", "/log"],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        else:
            return False, "MetaEditor not found"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (proc.stdout or "") + (proc.stderr or "")
    if log_path.exists():
        for encoding in ("utf-16", "utf-8", "cp1252"):
            try:
                detail = log_path.read_text(encoding=encoding, errors="replace")
                break
            except OSError:
                continue
    ok = mq5.with_suffix(".ex5").is_file()
    return ok, detail.strip()


def install() -> int:
    print(f"Detected OS: {paths.host_os()}")
    src = ea_source()
    if not src.is_file():
        print(f"Missing {src}", file=sys.stderr)
        return 1
    terminals = paths.list_mt5_terminals()
    if not terminals:
        print("No MetaTrader 5 data folder found.", file=sys.stderr)
        if paths.is_macos():
            print("Install MT5 (Wine app), log into a demo account, then run this again.", file=sys.stderr)
        else:
            print("Install MT5, log into a demo account, then run this again.", file=sys.stderr)
        print(f"Looked under: {paths.common_files_dir().parent.parent}", file=sys.stderr)
        return 1

    copied = 0
    compiled = 0
    for term in terminals:
        dest = term.experts_dir / EA_NAME
        dest.write_bytes(src.read_bytes())
        copied += 1
        via = "Wine" if term.wine_prefix else "native"
        print(f"Copied to ({via}): {dest}")
        if term.metaeditor_exe is None and term.wine_bin is None:
            print("  MetaEditor not found - open the file in MetaEditor and press F7.")
            continue
        ok, detail = _compile(term, dest)
        if ok:
            compiled += 1
            print(f"  Compiled: {dest.with_suffix('.ex5')}")
        else:
            print("  Compile did not produce .ex5 - open the file in MetaEditor and press F7.")
            if detail:
                last = "\n".join(detail.splitlines()[-8:])
                print(f"  {last}")

    print()
    print("In MT5: Navigator -> Expert Advisors -> Refresh,")
    print("reattach PythonBridgeEA on each enabled-symbol H4 chart, enable Algo Trading.")
    print("Leave EnableTcp=false when more than one chart is attached.")
    print("Do not attach ConservativeTrendEA at the same time.")
    print(f"Installed into {copied} terminal(s); compiled {compiled}.")
    return 0 if copied else 1
