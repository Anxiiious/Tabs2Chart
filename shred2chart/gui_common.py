"""Toolkit-agnostic helpers shared by the GUI.

Kept free of any Qt/Tk imports so the pure logic stays unit-testable without a
display, and so both the current Qt front end and the legacy Tk one can use it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import media

_GP_SUFFIXES = {".gp", ".gpx", ".gp3", ".gp4", ".gp5"}

# (label, space-separated glob pattern) pairs.  Tk consumes these directly; the
# Qt front end converts them with qt_filter().
_GP_FILETYPES = [
    ("Guitar Pro files", "*.gp *.gpx *.gp3 *.gp4 *.gp5"),
    ("All files", "*.*"),
]
_AUDIO_FILETYPES = [
    ("Audio files", " ".join(f"*{ext}" for ext in sorted(media.AUDIO_EXTENSIONS))),
    ("All files", "*.*"),
]
_IMAGE_FILETYPES = [
    ("Image files", " ".join(f"*{ext}" for ext in sorted(media.IMAGE_EXTENSIONS))),
    ("All files", "*.*"),
]
_MOONSCRAPER_FILETYPES = [
    ("MoonScraper Chart Editor", "Moonscraper Chart Editor.exe"),
    ("Windows applications", "*.exe"),
]

_CONFIG_PATH = Path.home() / ".shred2chart" / "gui_config.json"


def qt_filter(filetypes: list[tuple[str, str]]) -> str:
    """Render Tk-style filetype pairs as a Qt file-dialog filter string."""
    return ";;".join(f"{label} ({patterns})" for label, patterns in filetypes)


def _parse_dnd_path(data: str) -> str:
    """Return the first path from a Tk DND payload.

    Tk wraps paths containing spaces in braces and may send several paths in
    one payload.  The importer intentionally accepts only the first.
    """
    data = data.strip()
    match = re.match(r'^(?:\{([^}]*)\}|"([^"]*)"|(\S+))', data)
    if not match:
        return data
    return next(group for group in match.groups() if group is not None)


def _suggest_companion_files(gp_file: str | Path) -> tuple[Path | None, Path | None]:
    """Find same-folder audio and artwork that likely belong to *gp_file*."""
    path = Path(gp_file)
    if not path.is_file():
        return None, None

    audio = next(
        (
            path.with_suffix(ext)
            for ext in sorted(media.AUDIO_EXTENSIONS)
            if path.with_suffix(ext).is_file()
        ),
        None,
    )
    art = next(
        (
            path.with_suffix(ext)
            for ext in sorted(media.IMAGE_EXTENSIONS)
            if path.with_suffix(ext).is_file()
        ),
        None,
    )
    if art is None:
        for name in ("cover", "folder", "album"):
            art = next(
                (
                    path.parent / f"{name}{ext}"
                    for ext in sorted(media.IMAGE_EXTENSIONS)
                    if (path.parent / f"{name}{ext}").is_file()
                ),
                None,
            )
            if art is not None:
                break
    return audio, art


def _song_output_dir(root: str | Path, artist: str, title: str) -> Path:
    """Build the final song folder beneath a user-selected Songs directory."""
    from .cli import _safe_path_part  # noqa: PLC0415

    return Path(root).expanduser() / f"{_safe_path_part(artist)} - {_safe_path_part(title)}"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_config(config: dict) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
    except OSError:
        pass
