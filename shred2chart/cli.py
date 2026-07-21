"""Command-line entry point for shred2chart.

Run `shred2chart --help` (after installing, see README.md) to see all
commands. Every command prints plain, readable output - no GUI required.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import xml.etree.ElementTree as ET

from . import blend, chart_writer, gpif_tempo, gpx_reader, ir_gp, ir_gpif, mapper, media, tempo

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


def _cmd_list_tracks(args: argparse.Namespace) -> int:
    path = Path(args.gp_file)
    try:
        if path.suffix.lower() in _CONTAINER_SUFFIXES:
            xml_text = gpx_reader.extract_gpif(path)
            tracks = ir_gpif.list_tracks(xml_text)
        else:
            tracks = ir_gp.list_tracks(path)
    except (gpx_reader.GpxFormatError, gpif_tempo.GpifFormatError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error parsing {path}: {e}", file=sys.stderr)
        return 1

    for index, name in tracks:
        print(f"  {index}: {name}")
    print("\nPass the number you want to `dump-ir` with --track (default: 0).")
    return 0


def _dump_ir(path: Path, track_index: int) -> list[dict]:
    if path.suffix.lower() in _CONTAINER_SUFFIXES:
        xml_text = gpx_reader.extract_gpif(path)
        return ir_gpif.dump_ir(xml_text, track_index=track_index)
    return ir_gp.dump_ir(path, track_index=track_index)


def _cmd_dump_ir(args: argparse.Namespace) -> int:
    path = Path(args.gp_file)
    try:
        notes = _dump_ir(path, args.track)
    except (gpx_reader.GpxFormatError, gpif_tempo.GpifFormatError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error parsing {path}: {e}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(json.dumps(notes, indent=2), encoding="utf-8")
        print(f"wrote {args.out} ({len(notes)} notes)")
    else:
        print(json.dumps(notes, indent=2))
    return 0


def _guess_guitar_tracks(tracks: list[tuple[int, str]]) -> list[int]:
    """Default track selection for `convert` when --tracks isn't given:
    every track whose name suggests guitar, skipping bass/drums. Order
    is file order, which doubles as the blend tie-breaker."""
    chosen = []
    for track_id, name in tracks:
        lowered = (name or "").lower()
        if "bass" in lowered or "drum" in lowered:
            continue
        if "guit" in lowered or "lead" in lowered or "rhythm" in lowered:
            chosen.append(track_id)
    return chosen or [tracks[0][0]]


class ConvertError(Exception):
    """Raised by convert_song() for any recoverable input error (bad file,
    unknown track, etc). Callers (CLI, GUI) turn this into their own
    error display instead of an unhandled traceback."""


class ConvertResult:
    def __init__(self, out_dir: Path, title: str, artist: str, wrote_audio: bool, wrote_album_art: bool):
        self.out_dir = out_dir
        self.title = title
        self.artist = artist
        self.wrote_audio = wrote_audio
        self.wrote_album_art = wrote_album_art


def convert_song(
    gp_file: str | Path,
    out: str | Path | None = None,
    audio: str | Path | None = None,
    album_art: str | Path | None = None,
    track: int | None = None,
    tracks: str | None = None,
    overrides: str | None = None,
    lead_in_bars: int = 2,
    offset_ms: int = 0,
    on_progress=print,
) -> ConvertResult:
    """Convert a .gp/.gpx file into a Clone Hero song folder.

    This is the reusable core behind the `convert` CLI command - the CLI
    and the GUI both call this, so there is exactly one place the
    blend -> map -> emit -> audio/art pipeline is wired up. Progress and
    warnings are reported via on_progress(str) instead of print(), so a
    GUI can route them into a log pane. Raises ConvertError for any
    recoverable input problem (bad file, unknown track, etc); callers
    decide how to display that.

    overrides: optional "tick:track,tick:track,..." string (only used
    when blending multiple tracks). Each pair pins every sub-window from
    that IR tick onward to that track, bypassing the auto-blend scoring
    entirely - for the rare passage where no scoring heuristic picks the
    part the user actually wants (see blend.blend_tracks's docstring).
    """
    path = Path(gp_file)
    if path.suffix.lower() not in _CONTAINER_SUFFIXES:
        raise ConvertError(
            "convert currently supports .gp/.gpx files only (every real "
            "Sheet Happens tab seen so far is .gp). For .gp5, ask for this to be extended."
        )

    try:
        xml_text = gpx_reader.extract_gpif(path)
    except gpx_reader.GpxFormatError as e:
        raise ConvertError(str(e)) from e

    root = ET.fromstring(xml_text)
    title = (root.findtext("./Score/Title") or path.stem).strip() or path.stem
    artist = (root.findtext("./Score/Artist") or "Unknown Artist").strip() or "Unknown Artist"

    all_tracks = ir_gpif.list_tracks(xml_text)
    names = dict(all_tracks)
    known = {t for t, _ in all_tracks}

    if track is not None:
        if track not in known:
            lines = "\n".join(f"  {t}: {n}" for t, n in all_tracks)
            raise ConvertError(f"track {track} not in this file. Available:\n{lines}")
        track_ids = [track]
    elif tracks:
        try:
            track_ids = [int(t) for t in tracks.split(",")]
        except ValueError:
            raise ConvertError(f"--tracks must be comma-separated numbers, got {tracks!r}")
        unknown = [t for t in track_ids if t not in known]
        if unknown:
            lines = "\n".join(f"  {t}: {n}" for t, n in all_tracks)
            raise ConvertError(f"track(s) {unknown} not in this file. Available:\n{lines}")
    else:
        track_ids = _guess_guitar_tracks(all_tracks)

    on_progress(f"{title} - {artist}")

    tempo_events = gpif_tempo.dump_tempo_events(xml_text)
    sections = gpif_tempo.dump_sections(xml_text)

    if track is not None:
        # Single-track mode: chart it verbatim, no blending/section-switching -
        # for when auto-blending multiple tracks produces a jumbled chart.
        on_progress(f"charting track {track} ({names[track]}) only, no blending")
        blended = ir_gpif.dump_ir(xml_text, track_index=track)
        choices = []
    else:
        on_progress(f"blending tracks: {', '.join(f'{t} ({names[t]})' for t in track_ids)}")

        # Blend at section granularity; if the file has no section markers,
        # fall back to fixed 8-bar windows so blending still happens at a
        # phrase-ish scale instead of collapsing to one whole-song pick.
        blend_spans = sections
        if not blend_spans and len(track_ids) > 1:
            bar_starts, _, _ = gpif_tempo.compute_bar_grid(ET.fromstring(xml_text))
            blend_spans = [
                {"tick": bar_starts[i], "bar": i, "name": f"bars {i + 1}-{min(i + 8, len(bar_starts))}"}
                for i in range(0, len(bar_starts), 8)
            ]
            on_progress("(no section markers in file - blending in 8-bar windows instead)")

        parsed_overrides: list[tuple[int, int]] = []
        if overrides:
            for pair in overrides.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                try:
                    tick_str, track_str = pair.split(":")
                    parsed_overrides.append((int(tick_str), int(track_str)))
                except ValueError:
                    raise ConvertError(
                        f"--override entries must be 'tick:track', got {pair!r}"
                    )
            unknown = [t for _, t in parsed_overrides if t not in track_ids]
            if unknown:
                raise ConvertError(
                    f"--override track(s) {unknown} aren't in the blended track "
                    f"list {track_ids} (see --tracks)"
                )

        tracks_notes = {t: ir_gpif.dump_ir(xml_text, track_index=t) for t in track_ids}
        blended, choices = blend.blend_tracks(
            tracks_notes, track_ids, blend_spans, overrides=parsed_overrides or None
        )

    chart_notes = mapper.map_notes(blended)

    on_progress(f"\n{len(sections)} section(s), {len(blended)} notes"
                f"{'' if track is not None else ' after blending'}, "
                f"{len(chart_notes)} chart events:")
    for choice in choices:
        on_progress(f"  {choice['section']:<24} <- track {choice['track']} ({names[choice['track']]})")

    tempo_events, sections, chart_notes, lead_in_ms = chart_writer.add_lead_in(
        tempo_events, sections, chart_notes, bars=lead_in_bars
    )
    if lead_in_ms:
        on_progress(f"\nadded {lead_in_bars} lead-in bar(s) ({lead_in_ms}ms) before the first note")

    out_dir = Path(out) if out else Path(f"songs/{artist} - {title}")
    chart_writer.write_song_folder(
        out_dir, title, artist, tempo_events, sections, chart_notes,
        offset_ms=lead_in_ms + offset_ms,
    )
    on_progress(f"\nwrote {out_dir}/notes.chart and song.ini")

    wrote_audio = False
    if audio:
        audio_path = Path(audio)
        if not audio_path.exists():
            on_progress(f"warning: --audio file not found: {audio_path}")
        elif not media.ffmpeg_available():
            on_progress(
                "warning: ffmpeg not found on PATH, skipping audio conversion "
                f"(convert {audio_path.name} to song.ogg manually and drop it in {out_dir})"
            )
        else:
            written = media.convert_audio(audio_path, out_dir)
            if written:
                on_progress(f"wrote {written}")
                wrote_audio = True
            else:
                on_progress(f"warning: ffmpeg failed to convert {audio_path}")
    else:
        on_progress("Drop the song's audio in that folder as song.ogg, then copy the folder "
                    "into Clone Hero's Songs directory (or open notes.chart in Moonscraper).")

    wrote_album_art = False
    if album_art:
        art_path = Path(album_art)
        if not art_path.exists():
            on_progress(f"warning: --album-art file not found: {art_path}")
        else:
            written = media.place_album_art(art_path, out_dir)
            if written:
                on_progress(f"wrote {written}")
                wrote_album_art = True
            else:
                on_progress(
                    f"warning: could not place album art from {art_path} "
                    "(ffmpeg not found on PATH and source isn't a .png)"
                )

    return ConvertResult(out_dir, title, artist, wrote_audio, wrote_album_art)


def _cmd_convert(args: argparse.Namespace) -> int:
    try:
        convert_song(
            args.gp_file,
            out=args.out,
            audio=args.audio,
            album_art=args.album_art,
            track=args.track,
            tracks=args.tracks,
            overrides=args.override,
            lead_in_bars=args.lead_in_bars,
            offset_ms=args.offset_ms,
            on_progress=print,
        )
    except ConvertError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
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
            f"\nGO - every tempo/time-signature event in {gpx_path.name} has a matching one in "
            f"{gp_path.name}. Route A (external conversion) looks safe for this file."
        )
    else:
        print(
            f"\nNO-GO (or partial) - {len(original_only)} event(s) only in the original, "
            f"{len(converted_only)} only in the converted file. See SHRED2CHART_GAMEPLAN.md "
            "section 3 of the Route B fallback (direct GPIF parsing skips this conversion step "
            "entirely, and already works for GP7 '.gp' files - see `dump-tempo`)."
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

    p_tracks = sub.add_parser(
        "list-tracks", help="list a file's tracks and their index, to pick one for dump-ir"
    )
    p_tracks.add_argument("gp_file", help="path to a .gp/.gpx/.gp3/.gp4/.gp5 file")
    p_tracks.set_defaults(func=_cmd_list_tracks)

    p_ir = sub.add_parser(
        "dump-ir",
        help="dump per-note data (tick, pitch, string, fret, techniques) for one track",
    )
    p_ir.add_argument("gp_file", help="path to a .gp/.gpx/.gp3/.gp4/.gp5 file")
    p_ir.add_argument(
        "--track", type=int, default=0, help="track index to dump (see `list-tracks`; default: 0)"
    )
    p_ir.add_argument("-o", "--out", help="write JSON to this path instead of stdout")
    p_ir.set_defaults(func=_cmd_dump_ir)

    p_convert = sub.add_parser(
        "convert",
        help="convert a .gp/.gpx file into a Clone Hero song folder (notes.chart + song.ini)",
    )
    p_convert.add_argument("gp_file", help="path to a .gp (GP7/8) or .gpx (GP6) file")
    p_track_group = p_convert.add_mutually_exclusive_group()
    p_track_group.add_argument(
        "--tracks",
        help="comma-separated track numbers to blend, in priority order (see `list-tracks`); "
        "default: every guitar-named track, blended per section",
    )
    p_track_group.add_argument(
        "--track", type=int,
        help="chart exactly this one track, verbatim - no blending/section-switching logic "
        "at all (see `list-tracks` for the index). Use this if auto-blending multiple "
        "tracks produces a jumbled chart.",
    )
    p_convert.add_argument(
        "--override",
        help="comma-separated 'tick:track' pairs (IR ticks, 960/quarter); each pins every "
        "sub-window from that tick onward to that track, bypassing auto-blend scoring - for "
        "a passage where the automatic pick isn't the one you want (only applies when "
        "blending multiple tracks, i.e. not with --track)",
    )
    p_convert.add_argument("-o", "--out", help="output folder (default: songs/Artist - Title)")
    p_convert.add_argument(
        "--offset-ms", type=int, default=0,
        help="extra audio offset in milliseconds, on top of --lead-in-bars "
        "(for fine-tuning after calibrating in Moonscraper; default 0)",
    )
    p_convert.add_argument(
        "--lead-in-bars", type=int, default=2,
        help="bars of silence to insert before the first note, so the highway "
        "scrolls before play starts and Clone Hero's audio calibration has "
        "something to judge against (default 2; 0 disables)",
    )
    p_convert.add_argument(
        "--audio",
        help="path to the song's audio file (any format ffmpeg can read); "
        "converted to song.ogg in the output folder automatically (requires ffmpeg on PATH)",
    )
    p_convert.add_argument(
        "--album-art",
        help="path to cover art (png/jpg/etc); placed as album.png in the output folder "
        "(non-png sources require ffmpeg on PATH)",
    )
    p_convert.set_defaults(func=_cmd_convert)

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
