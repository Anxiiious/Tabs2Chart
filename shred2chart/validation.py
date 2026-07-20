"""Validation for generated Moon Scraper hand-off artifacts."""
from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any


def validate_song_folder(
    out_dir: str | Path,
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    audio_required: bool = False,
) -> list[str]:
    """Return compatibility errors without requiring Moon Scraper itself."""
    output = Path(out_dir)
    errors: list[str] = []
    chart = output / "notes.chart"
    song_ini = output / "song.ini"
    audio = output / "song.ogg"
    if not chart.is_file():
        errors.append(f"missing chart: {chart}")
    elif "[Song]" not in chart.read_text(encoding="utf-8"):
        errors.append("notes.chart is missing its [Song] section")
    if not song_ini.is_file():
        errors.append(f"missing metadata: {song_ini}")
    else:
        parser = configparser.ConfigParser()
        parser.read(song_ini, encoding="utf-8")
        if parser.get("song", "name", fallback="") != title:
            errors.append("song.ini name does not match chart metadata")
        if parser.get("song", "artist", fallback="") != artist:
            errors.append("song.ini artist does not match chart metadata")
    if not tempo_events:
        errors.append("timing map contains no tempo or time-signature events")
    if audio_required and not audio.is_file():
        errors.append(f"missing audio: {audio}")
    return errors
