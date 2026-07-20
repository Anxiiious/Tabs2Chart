"""Machine-readable output and Moon Scraper integration support."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "https://tabs2chart.dev/schemas/moon-scraper-manifest.v1.json"


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
    argv = shlex.split(command)
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
        raise RuntimeError(json.dumps(result))
    return result
