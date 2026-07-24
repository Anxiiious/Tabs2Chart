"""Locate and launch the stock MoonScraper Chart Editor on Windows."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class MoonscraperLaunchError(RuntimeError):
    """Raised when a generated chart cannot be opened in MoonScraper."""


def _registry_candidates() -> Iterable[Path]:
    """Yield install paths advertised by MoonScraper's Windows uninstaller."""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return

    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    for hive, key_name in roots:
        try:
            with winreg.OpenKey(hive, key_name) as root:
                subkey_count = winreg.QueryInfoKey(root)[0]
                for index in range(subkey_count):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                            display_name = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                            if "moonscraper" not in display_name.lower():
                                continue
                            install_dir = Path(winreg.QueryValueEx(entry, "InstallLocation")[0])
                            yield install_dir / "Moonscraper Chart Editor.exe"
                    except (OSError, ValueError):
                        continue
        except OSError:
            continue


def _common_candidates() -> Iterable[Path]:
    executable_name = "Moonscraper Chart Editor.exe"
    # Standalone Tabs2Chart.exe ships beside the optional custom editor folder.
    yield Path(sys.executable).resolve().parent / "Moonscraper-Tabs2Chart" / executable_name
    # Development checkout fallback.
    yield Path(__file__).resolve().parents[1] / "dist" / "Moonscraper-Tabs2Chart" / executable_name

    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if not root:
            continue
        root_path = Path(root)
        yield root_path / "Moonscraper Chart Editor" / executable_name
        yield root_path / "Programs" / "Moonscraper Chart Editor" / executable_name

    on_path = shutil.which(executable_name)
    if on_path:
        yield Path(on_path)


def find_moonscraper(configured: str | Path | None = None) -> Path | None:
    """Return a valid configured or discovered MoonScraper executable."""
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return configured_path.resolve()

    for candidate in (*_common_candidates(), *_registry_candidates()):
        if candidate.is_file():
            return candidate.resolve()
    return None


def open_chart(
    chart_path: str | Path,
    executable: str | Path,
    *,
    manifest_path: str | Path | None = None,
    popen=subprocess.Popen,
):
    """Launch MoonScraper and ask it to load *chart_path*.

    Stock MoonScraper scans its command-line arguments for the first existing
    file with a supported chart extension, so the chart path is passed as a
    normal second process argument.
    """
    chart = Path(chart_path).expanduser()
    if not chart.is_file():
        raise MoonscraperLaunchError(f"Chart does not exist: {chart}")
    if chart.suffix.lower() != ".chart":
        raise MoonscraperLaunchError(f"Expected a .chart file, got: {chart}")

    app = Path(executable).expanduser()
    if not app.is_file():
        raise MoonscraperLaunchError(f"MoonScraper executable does not exist: {app}")

    argv = [str(app.resolve())]
    if manifest_path is not None:
        manifest = Path(manifest_path).expanduser()
        if manifest.is_file():
            argv.extend(["--tabs2chart-manifest", str(manifest.resolve())])
    argv.append(str(chart.resolve()))

    try:
        return popen(argv, cwd=str(chart.parent.resolve()))
    except OSError as exc:
        raise MoonscraperLaunchError(f"Could not start MoonScraper: {exc}") from exc
