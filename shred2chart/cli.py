"""Command-line entry point for shred2chart.

Run `shred2chart --help` (after installing, see README.md) to see all
commands. Every command prints plain, readable output — no GUI required.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import gpx_reader, tempo


def _cmd_dump_gpif(args: argparse.Namespace) -> int:
    gpx_path = Path(args.gpx_file)
    try:
        xml_text = gpx_reader.extract_gpif(gpx_path)
    except gpx_reader.GpxFormatError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else gpx_path.with_suffix(".gpif")
    out_path.write_text(xml_text, encoding="utf-8")
    print(f"wrote {out_path} ({len(xml_text)} chars)")
    print("Open it in any text editor and search for 'Tempo' to see the tempo automations.")
    return 0


def _cmd_dump_tempo(args: argparse.Namespace) -> int:
    try:
        events = tempo.dump_tempo_events(args.gp_file)
    except Exception as e:  # PyGuitarPro raises plain exceptions on bad files
        print(f"error parsing {args.gp_file}: {e}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(json.dumps(events, indent=2), encoding="utf-8")
        print(f"wrote {args.out} ({len(events)} events)")
    else:
        print(json.dumps(events, indent=2))
    return 0


def _cmd_verify_m0(args: argparse.Namespace) -> int:
    gpx_path = Path(args.gpx_file)
    gp_path = Path(args.gp_file)

    print(f"== M0 check: {gpx_path.name} vs {gp_path.name} ==\n")

    gpif_out = gpx_path.with_suffix(".gpif")
    try:
        xml_text = gpx_reader.extract_gpif(gpx_path)
        gpif_out.write_text(xml_text, encoding="utf-8")
        print(f"[1/2] extracted score.gpif -> {gpif_out}")
    except gpx_reader.GpxFormatError as e:
        print(f"[1/2] FAILED to extract score.gpif from {gpx_path}: {e}", file=sys.stderr)
        return 1

    try:
        events = tempo.dump_tempo_events(gp_path)
    except Exception as e:
        print(f"[2/2] FAILED to parse {gp_path}: {e}", file=sys.stderr)
        return 1

    tempo_out = gp_path.with_suffix(".tempo.json")
    tempo_out.write_text(json.dumps(events, indent=2), encoding="utf-8")
    tempo_events = [e for e in events if e["type"] == "tempo"]
    print(f"[2/2] extracted {len(tempo_events)} tempo event(s) from {gp_path.name} -> {tempo_out}\n")

    for e in tempo_events:
        print(f"  tick {e['tick']:>7}  ->  {e['bpm']} bpm")

    print(
        "\nNext step (manual, this is the actual M0 test):\n"
        f"  1. Open {gpif_out.name} in a text editor and search for 'Tempo'.\n"
        f"  2. Also open the original {gpx_path.name} in Guitar Pro/TuxGuitar and look at its tempo track.\n"
        f"  3. Compare both against the {len(tempo_events)} tempo value(s) printed above.\n"
        "  If they line up (same bar positions, same BPM values, no missing changes), that's a GO on Route A.\n"
        "  If tempo data is missing or wrong, tell your coding agent — that's a NO-GO, and we fall back to Route B."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shred2chart",
        description="Convert Sheet Happens Guitar Pro tabs into Clone Hero charts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gpif = sub.add_parser("dump-gpif", help="extract score.gpif XML from a .gpx file")
    p_gpif.add_argument("gpx_file", help="path to a .gpx file")
    p_gpif.add_argument("-o", "--out", help="output path (default: alongside input, .gpif)")
    p_gpif.set_defaults(func=_cmd_dump_gpif)

    p_tempo = sub.add_parser(
        "dump-tempo", help="dump tempo/time-signature events from a .gp3/.gp4/.gp5 file"
    )
    p_tempo.add_argument("gp_file", help="path to a .gp3/.gp4/.gp5 file")
    p_tempo.add_argument("-o", "--out", help="write JSON to this path instead of stdout")
    p_tempo.set_defaults(func=_cmd_dump_tempo)

    p_verify = sub.add_parser(
        "verify-m0",
        help="M0 check: compare a .gpx's tempo data against its converted .gp5",
    )
    p_verify.add_argument("gpx_file", help="the original .gpx file")
    p_verify.add_argument("gp_file", help="the same song, converted to .gp3/.gp4/.gp5")
    p_verify.set_defaults(func=_cmd_verify_m0)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
