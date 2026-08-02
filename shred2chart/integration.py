"""Machine-readable output and Moon Scraper integration support."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "https://tabs2chart.dev/schemas/moon-scraper-manifest.v1.json"
IMPORT_SETTINGS_FILENAME = "tabs2chart-import.json"


def write_import_settings(out_dir: str | Path, settings: dict[str, Any]) -> Path:
    """Persist the editable inputs used to generate one song folder."""
    output = Path(out_dir)
    path = output / IMPORT_SETTINGS_FILENAME
    payload = {"version": 1, **settings}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_import_settings(song_dir: str | Path) -> dict[str, Any]:
    """Load settings from a generated song folder for GUI re-editing."""
    path = Path(song_dir)
    if path.is_dir():
        path = path / IMPORT_SETTINGS_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read {IMPORT_SETTINGS_FILENAME}: {exc}") from exc
    if payload.get("version") != 1 or not isinstance(payload.get("inputs"), dict):
        raise ValueError(f"{IMPORT_SETTINGS_FILENAME} is not a supported Tabs2Chart import record")
    return payload


def _split_command(command: str) -> list[str]:
    """Split a user-entered command without eating Windows path separators."""
    if os.name != "nt":
        return shlex.split(command)

    lexer = shlex.shlex(command, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    argv = list(lexer)
    return [
        arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in {'"', "'"} else arg
        for arg in argv
    ]


def write_manifest(
    out_dir: str | Path,
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chart_notes: int,
    offset_ms: int = 0,
    audio_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write the stable hand-off document consumed by a Moon Scraper fork."""
    output = Path(out_dir).resolve()
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "version": 1,
        "source": {"title": title, "artist": artist},
        "output": {
            "directory": str(output),
            "chart": str(output / "notes.chart"),
            "song_ini": str(output / "song.ini"),
            "audio": str(output / "song.ogg") if audio_path else None,
        },
        "timing": {
            "offset_ms": offset_ms,
            "tempo_events": tempo_events,
            "sections": sections,
        },
        "chart": {"note_events": chart_notes, "format": "clone-hero-chart"},
        "audio": {
            "required_filename": "song.ogg",
            "source": str(Path(audio_path).resolve()) if audio_path else None,
        },
    }
    manifest_path = output / "moon-scraper-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest


def invoke_moon_scraper(
    manifest_path: str | Path,
    command: str,
    timeout: int = 300,
) -> dict[str, Any]:
    """Invoke a fork through a subprocess boundary using the manifest on stdin."""
    manifest = Path(manifest_path).resolve()
    if not manifest.is_file():
        raise FileNotFoundError(f"manifest does not exist: {manifest}")
    argv = _split_command(command)
    if not argv:
        raise ValueError("Moon Scraper command cannot be empty")
    completed = subprocess.run(
        argv,
        input=manifest.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    result: dict[str, Any] = {
        "command": argv,
        "manifest": str(manifest),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        detail = " ".join(detail.split()).encode("utf-8")[:1000].decode(
            "utf-8", errors="ignore"
        )
        raise RuntimeError(f"command exited with {completed.returncode}: {detail}")
    return result
