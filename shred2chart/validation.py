"""Validation for generated Moon Scraper hand-off artifacts."""
from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Any


def escape_metadata(value: str) -> str:
    """Escape a metadata string for safe embedding in .chart / song.ini values.

    Strips ASCII control characters (0x00-0x1F, 0x7F) and escapes backslash
    and double-quote characters so downstream parsers (Clone Hero, Moonscraper)
    can safely read the values.
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", value)
    return cleaned.replace("\\", "\\\\").replace('"', '\\"')


def validate_song_folder(
    out_dir: str | Path,
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    audio_required: bool = False,
) -> list[str]:
    """Return compatibility errors without requiring Moon Scraper itself.

    Checks performed:
    - notes.chart is present and has [Song] and [ExpertSingle] sections.
    - [ExpertSingle] is non-empty (has at least one note event).
    - song.ini is present with matching name/artist.
    - tempo_events is non-empty.
    - song.ogg is present when audio_required=True.
    """
    output = Path(out_dir)
    errors: list[str] = []
    chart = output / "notes.chart"
    song_ini = output / "song.ini"
    audio = output / "song.ogg"

    if not chart.is_file():
        errors.append(f"missing chart: {chart}")
    else:
        chart_text = chart.read_text(encoding="utf-8")
        if "[Song]" not in chart_text:
            errors.append(f"{chart} is missing its [Song] section")
        if "[ExpertSingle]" not in chart_text:
            errors.append(f"{chart} is missing the [ExpertSingle] section")
        else:
            # Check that [ExpertSingle] contains at least one note line (N <lane>)
            in_expert = False
            has_notes = False
            for line in chart_text.splitlines():
                stripped = line.strip()
                if stripped == "[ExpertSingle]":
                    in_expert = True
                elif in_expert and stripped.startswith("["):
                    break
                elif in_expert and re.search(r"=\s*N\s+\d", stripped):
                    has_notes = True
                    break
            if not has_notes:
                errors.append(f"{chart} [ExpertSingle] section has no note events")

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
        errors.append("timing map contains no tempo events")

    if audio_required and not audio.is_file():
        errors.append(f"missing audio: {audio}")

    return errors
