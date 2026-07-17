"""Command-line entry point for shred2chart.

Run `shred2chart --help` (after installing, see README.md) to see all
commands. Every command prints plain, readable output — no GUI required.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import gpif_tempo, gpx_reader, tempo

_CONTAINER_SUFFIXES = {".gp", ".gpx"}


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


def _dump_tempo_events(path: Path) -> list[dict]:
    """Dispatch to the right extractor based on file type: a .gp/.gpx
    container is read directly (no conversion needed), anything else is
    assumed to be a .gp3/.gp4/.gp5 file PyGuitarPro can parse."""
    if path.suffix.lower() in _CONTAINER_SUFFIXES:
        xml_text = gpx_reader.extract_gpif(path)
        return gpif_tempo.dump_tempo_events(xml_text)
    return tempo.dump_tempo_events(path)


def _cmd_dump_tempo(args: argparse.Namespace) -> int:
    path = Path(args.gp_file)
    try:
        events = _dump_tempo_events(path)
    except (gpx_reader.GpxFormatError, gpif_tempo.GpifFormatError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # PyGuitarPro raises plain exceptions on bad files
        print(f"error parsing {path}: {e}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(json.dumps(events, indent=2), encoding="utf-8")
        print(f"wrote {args.out} ({len(events)} events)")
    else:
        print(json.dumps(events, indent=2))
    return 0


def _event_key(event: dict) -> tuple:
    return (event["type"], event.get("bpm"), event.get("numerator"), event.get("denominator"))


def _diff_events(
    original: list[dict], converted: list[dict], tick_tolerance: int = 2
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Match events between the two lists by (type, value) with a small
    tick tolerance for rounding. Returns (matched pairs, original-only,
    converted-only)."""
    unmatched_converted = list(converted)
    matched = []
    unmatched_original = []
    for event in original:
        match = next(
            (
                c for c in unmatched_converted
                if _event_key(c) == _event_key(event) and abs(c["tick"] - event["tick"]) <= tick_tolerance
            ),
            None,
        )
        if match is not None:
            matched.append((event, match))
            unmatched_converted.remove(match)
        else:
            unmatched_original.append(event)
    return matched, unmatched_original, unmatched_converted


def _cmd_verify_m0(args: argparse.Namespace) -> int:
    gpx_path = Path(args.gpx_file)
    gp_path = Path(args.gp_file)

    print(f"== M0 check: {gpx_path.name} (direct) vs {gp_path.name} (converted) ==\n")

    try:
        xml_text = gpx_reader.extract_gpif(gpx_path)
        gpif_out = gpx_path.with_suffix(".gpif")
        gpif_out.write_text(xml_text, encoding="utf-8")
        original_events = gpif_tempo.dump_tempo_events(xml_text)
        print(f"[1/3] read {gpx_path.name} directly: {len(original_events)} tempo/TS event(s) -> {gpif_out}")
    except (gpx_reader.GpxFormatError, gpif_tempo.GpifFormatError) as e:
        print(f"[1/3] FAILED to read {gpx_path}: {e}", file=sys.stderr)
        return 1

    try:
        converted_events = tempo.dump_tempo_events(gp_path)
        print(f"[2/3] parsed {gp_path.name} via PyGuitarPro: {len(converted_events)} tempo/TS event(s)")
    except Exception as e:
        print(f"[2/3] FAILED to parse {gp_path}: {e}", file=sys.stderr)
        return 1

    matched, original_only, converted_only = _diff_events(original_events, converted_events)
    print("[3/3] comparing...\n")
    for original_event, converted_event in matched:
        print(f"  match       tick {original_event['tick']:>7} -> {converted_event['tick']:>7}   {original_event}")
    for event in original_only:
        print(f"  ONLY IN {gpx_path.name}   tick {event['tick']:>7}   {event}")
    for event in converted_only:
        print(f"  ONLY IN {gp_path.name}   tick {event['tick']:>7}   {event}")

    if not original_only and not converted_only:
        print(
            f"\nGO — every tempo/time-signature event in {gpx_path.name} has a matching one in "
            f"{gp_path.name}. Route A (external conversion) looks safe for this file."
        )
    else:
        print(
            f"\nNO-GO (or partial) — {len(original_only)} event(s) only in the original, "
            f"{len(converted_only)} only in the converted file. See SHRED2CHART_GAMEPLAN.md "
            "§3 for the Route B fallback (direct GPIF parsing skips this conversion step "
            "entirely, and already works for GP7 '.gp' files — see `dump-tempo`)."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shred2chart",
        description="Convert Sheet Happens Guitar Pro tabs into Clone Hero charts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gpif = sub.add_parser("dump-gpif", help="extract score.gpif XML from a .gp/.gpx file")
    p_gpif.add_argument("gpx_file", help="path to a .gp (GP7/8) or .gpx (GP6) file")
    p_gpif.add_argument("-o", "--out", help="output path (default: alongside input, .gpif)")
    p_gpif.set_defaults(func=_cmd_dump_gpif)

    p_tempo = sub.add_parser(
        "dump-tempo",
        help="dump tempo/time-signature events from a .gp/.gpx/.gp3/.gp4/.gp5 file",
    )
    p_tempo.add_argument(
        "gp_file",
        help="path to a .gp/.gpx file (read directly) or a .gp3/.gp4/.gp5 file (via PyGuitarPro)",
    )
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
