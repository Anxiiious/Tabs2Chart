"""CLI for shred2chart.

Usage::

    shred2chart input.gp5 --out ./songs/"Artist - Title"/
    shred2chart input.gp5 --out ./out/ --offset-ms -150 --track "Lead Guitar"
    shred2chart --dump-tempo input.gp5   # M0 validation helper

Route A note:
  .gpx files must be pre-converted to .gp5 (Guitar Pro or TuxGuitar).
  Route B (direct .gpx parse) is deferred to v1.1.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shred2chart",
        description=(
            "Convert Guitar Pro tabs (.gp5 / .gp4 / .gp3) into "
            "Clone Hero charts (.chart + song.ini)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  shred2chart song.gp5 --out ./songs/Artist\\ -\\ Title/\n"
            "  shred2chart song.gp5 --out ./out/ --offset-ms -150\n"
            "  shred2chart --dump-tempo song.gp5   # M0 tempo-validation dump\n"
        ),
    )

    parser.add_argument("input", help="Guitar Pro file (.gp3/.gp4/.gp5)")

    # ── Output ────────────────────────────────────────────────────────────────
    out_group = parser.add_argument_group("output options")
    out_group.add_argument(
        "--out", "-o",
        metavar="DIR",
        default=".",
        help="Destination folder for notes.chart and song.ini (default: .)",
    )
    out_group.add_argument(
        "--offset-ms",
        type=int,
        default=0,
        metavar="MS",
        help="Global audio offset in milliseconds (written to song.ini delay).",
    )
    out_group.add_argument(
        "--charter",
        default="shred2chart",
        metavar="NAME",
        help="Charter name written to song.ini (default: shred2chart).",
    )

    # ── Track selection ───────────────────────────────────────────────────────
    track_group = parser.add_argument_group("track / tuning options")
    track_group.add_argument(
        "--track",
        metavar="NAME",
        default="",
        help="Name of the GP track to use (case-insensitive). "
             "Defaults to first track whose name contains 'lead' or 'guitar'.",
    )
    track_group.add_argument(
        "--open-strings",
        nargs="+",
        type=int,
        default=[6],
        metavar="N",
        help="String numbers where fret 0 maps to CH open note. "
             "Default: 6 (lowest string of a 6-string guitar). "
             "For 7-string Drop A, add 7.",
    )

    # ── Mapping knobs ─────────────────────────────────────────────────────────
    map_group = parser.add_argument_group("mapping knobs")
    map_group.add_argument(
        "--phrase-boundary",
        type=float,
        default=1.0,
        metavar="BEATS",
        help="Rest length (in beats) that resets the contour window (default: 1.0).",
    )
    map_group.add_argument(
        "--max-chord-width",
        type=int,
        default=3,
        metavar="N",
        help="Maximum lane span for chords (default: 3).",
    )
    map_group.add_argument(
        "--sustain-threshold",
        type=float,
        default=0.125,
        metavar="BEATS",
        help="Notes shorter than this fraction of a beat get 0 sustain (default: 0.125).",
    )

    # ── Diagnostics ───────────────────────────────────────────────────────────
    diag_group = parser.add_argument_group("diagnostics")
    diag_group.add_argument(
        "--dump-tempo",
        action="store_true",
        help="Print tempo events as JSON (M0 validation) and exit.",
    )
    diag_group.add_argument(
        "--dump-ir",
        action="store_true",
        help="Print IR event summary as JSON and exit.",
    )
    diag_group.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # Late imports to avoid paying the PyGuitarPro import cost for --help.
    from .config import Config
    from .ingest import dump_tempo_events, parse_gp_file
    from .mapping import map_notes
    from .synctrack import build_synctrack
    from .emit import write_song_folder

    # ── M0 validation helper ──────────────────────────────────────────────────
    if args.dump_tempo:
        events = dump_tempo_events(args.input)
        print(json.dumps(events, indent=2))
        return 0

    # ── Build Config ──────────────────────────────────────────────────────────
    config = Config(
        open_strings=args.open_strings,
        phrase_boundary_beats=args.phrase_boundary,
        max_chord_width=args.max_chord_width,
        sustain_threshold_beats=args.sustain_threshold,
        offset_ms=args.offset_ms,
        charter=args.charter,
        track_name=args.track,
    )

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        ir = parse_gp_file(args.input, config)
    except Exception as exc:
        print(f"ERROR: Could not parse {args.input!r}: {exc}", file=sys.stderr)
        return 1

    # ── IR dump helper ────────────────────────────────────────────────────────
    if args.dump_ir:
        import dataclasses
        payload = {
            "title": ir.title,
            "artist": ir.artist,
            "resolution": ir.resolution,
            "string_count": ir.string_count,
            "tuning": ir.tuning,
            "note_count": len(ir.notes),
            "tempo_events": [dataclasses.asdict(t) for t in ir.tempo_events],
            "time_signatures": [dataclasses.asdict(t) for t in ir.time_signatures],
            "sections": [dataclasses.asdict(s) for s in ir.sections],
            "first_notes": [dataclasses.asdict(n) for n in ir.notes[:20]],
        }
        print(json.dumps(payload, indent=2))
        return 0

    # ── Pipeline ──────────────────────────────────────────────────────────────
    sync_events = build_synctrack(ir)
    chart_notes = map_notes(ir, config)

    write_song_folder(
        out_dir=args.out,
        ir=ir,
        sync_events=sync_events,
        chart_notes=chart_notes,
        config=config,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
