"""Command-line entry point for shred2chart.

Run `shred2chart --help` (after installing, see README.md) to see all
commands. Every command prints plain, readable output - no GUI required.
"""
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import argparse
import configparser
import dataclasses
import json
import logging
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable

import xml.etree.ElementTree as ET

try:
    import argcomplete  # type: ignore[import-untyped]
    _ARGCOMPLETE_AVAILABLE = True
except ImportError:
    _ARGCOMPLETE_AVAILABLE = False

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("shred2chart")
except Exception:
    __version__ = "0.0.0"

from . import (
    blend, chart_writer, gpif_tempo, gpx_reader, integration, ir_gp, ir_gpif,
    mapper, media, tempo, validation,
)

_CONTAINER_SUFFIXES = {".gp", ".gpx"}
_LEGACY_SUFFIXES = {".gp3", ".gp4", ".gp5"}
_ALL_SUFFIXES = _CONTAINER_SUFFIXES | _LEGACY_SUFFIXES

log = logging.getLogger("shred2chart")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validate_input_file(
    path: Path,
    accepted_suffixes: set[str] | None = None,
) -> int:
    """Check that *path* exists, is a file, and (optionally) has an accepted
    extension.  Prints a user-friendly error to stderr and returns 1 on
    failure, 0 on success."""
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 1
    if accepted_suffixes is not None and path.suffix.lower() not in accepted_suffixes:
        supported = ", ".join(sorted(accepted_suffixes))
        print(
            f"error: unsupported file type '{path.suffix}' for {path.name} "
            f"(supported: {supported})",
            file=sys.stderr,
        )
        return 1
    return 0


def _configure_logging(args: argparse.Namespace) -> None:
    verbose = getattr(args, "verbose", False)
    quiet = getattr(args, "quiet", False)
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def _cmd_dump_gpif(args: argparse.Namespace) -> int:
    gpx_path = Path(args.gpx_file)
    if _validate_input_file(gpx_path, _CONTAINER_SUFFIXES):
        return 1
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
    if _validate_input_file(path, _ALL_SUFFIXES):
        return 1
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
    if _validate_input_file(path, _ALL_SUFFIXES):
        return 1
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
    print("Pass a comma-separated list to `convert` with --tracks (e.g. --tracks 1,0).")
    return 0


def _dump_ir(path: Path, track_index: int) -> list[dict]:
    if path.suffix.lower() in _CONTAINER_SUFFIXES:
        xml_text = gpx_reader.extract_gpif(path)
        return ir_gpif.dump_ir(xml_text, track_index=track_index)
    return ir_gp.dump_ir(path, track_index=track_index)


def _cmd_dump_ir(args: argparse.Namespace) -> int:
    path = Path(args.gp_file)
    if _validate_input_file(path, _ALL_SUFFIXES):
        return 1
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


def _safe_path_part(value: str) -> str:
    """Keep metadata-derived output paths within the songs directory."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]', "_", value).strip(" .")
    return cleaned or "Untitled"


def _default_output_dir(artist: str, title: str) -> Path:
    return Path("songs") / f"{_safe_path_part(artist)} - {_safe_path_part(title)}"


def _ffmpeg_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "  Install with: brew install ffmpeg"
    if system == "Windows":
        return "  Install from https://ffmpeg.org/download.html or: winget install ffmpeg"
    return "  Install with: sudo apt install ffmpeg  (or your distro's package manager)"


def _find_ffmpeg() -> str | None:
    """Look for ffmpeg on PATH, then bundled next to the running app.

    PyInstaller builds ship ffmpeg.exe under an `ffmpeg/bin` folder next to
    the frozen executable (see shred2chart.spec); dev/source checkouts have
    the same layout at the repo root.
    """
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent)
        meipass = getattr(sys, "_MEIPASS", None)  # PyInstaller onefile extraction dir
        if meipass:
            candidates.append(Path(meipass))
    else:
        candidates.append(Path(__file__).resolve().parent.parent)
    for base_dir in candidates:
        bundled = base_dir / "ffmpeg" / "bin" / "ffmpeg.exe"
        if bundled.is_file():
            return str(bundled)
    return None


def _prepare_audio(audio: str | Path, out_dir: Path) -> Path:
    source = Path(audio).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"audio file does not exist: {source}")
    target = out_dir / "song.ogg"
    if source.suffix.lower() == ".ogg":
        shutil.copy2(source, target)
        return target
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "non-OGG audio requires ffmpeg on PATH\n"
            + _ffmpeg_install_hint()
        )
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-vn", "-acodec", "libvorbis", str(target)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or "unknown ffmpeg error"
        raise RuntimeError(f"ffmpeg could not convert audio: {detail}") from exc
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("ffmpeg ran but produced an empty or missing song.ogg")
    return target


def _prompt_convert_options(
    args: argparse.Namespace,
    tracks: list[tuple[int, str]],
    track_ids: list[int],
    names: dict[int, str],
    artist: str,
    title: str,
) -> tuple[list[int], Path] | None:
    """Let an interactive user review track and output choices before writing."""
    print("\nInteractive conversion")
    print("Available tracks:")
    for track_id, name in tracks:
        marker = "*" if track_id in track_ids else " "
        print(f"  {marker} {track_id}: {name}")

    default_tracks = ",".join(str(track_id) for track_id in track_ids)
    while True:
        selected = input(f"Tracks to blend [{default_tracks}]: ").strip() or default_tracks
        try:
            selected_ids = [int(track_id.strip()) for track_id in selected.split(",")]
        except ValueError:
            print("Please enter comma-separated track numbers.")
            continue
        unknown = [track_id for track_id in selected_ids if track_id not in names]
        if unknown:
            print(f"Unknown track(s): {', '.join(map(str, unknown))}")
            continue
        break

    if not getattr(args, "audio", None):
        print("Provide an audio file named song.ogg in the output folder before playing.")
    default_out = Path(args.out) if args.out else _default_output_dir(artist, title)
    out_text = input(f"Output folder [{default_out}]: ").strip()
    out_dir = Path(out_text).expanduser() if out_text else default_out
    if out_dir.exists() and any(out_dir.iterdir()):
        answer = input(
            f"{out_dir} is not empty. Overwrite its files? [y/N, default: No]: "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled; no files were written.")
            return None

    print("Use --offset-ms if the chart needs audio-sync adjustment later.")
    return selected_ids, out_dir


def _get_gpx_metadata(xml_text: str, path: Path) -> tuple[str, str, str]:
    """Return (title, artist, charter) from score.gpif XML."""
    root = ET.fromstring(xml_text)
    title = (root.findtext("./Score/Title") or path.stem).strip() or path.stem
    artist = (root.findtext("./Score/Artist") or "Unknown Artist").strip() or "Unknown Artist"
    # GP files sometimes carry a "Credit" or "SubTitle" field — use as charter hint
    credit = (root.findtext("./Score/SubTitle") or "").strip()
    return title, artist, credit


class ConvertError(Exception):
    """Raised by convert_song() for any user-facing conversion failure."""


@dataclasses.dataclass
class ConvertResult:
    out_dir: Path
    title: str
    artist: str
    manifest: dict


def peek_metadata(gp_file: str | Path) -> tuple[str, str]:
    """Return (artist, title) for *gp_file* without doing a full conversion.

    Used by the GUI to preview the auto-generated output folder name as
    soon as a file is picked, before Convert is clicked.
    """
    path = Path(gp_file)
    suffix = path.suffix.lower()
    if suffix in _CONTAINER_SUFFIXES:
        xml_text = gpx_reader.extract_gpif(path)
        title, artist, _credit = _get_gpx_metadata(xml_text, path)
        return artist, title
    if suffix in _LEGACY_SUFFIXES:
        import guitarpro  # noqa: PLC0415
        song = guitarpro.parse(str(path))
        title = getattr(song, "title", "") or path.stem
        artist = getattr(song, "artist", "") or "Unknown Artist"
        return artist, title
    raise ConvertError(f"unsupported file type '{path.suffix}' for {path.name}")


def convert_song(
    gp_file: str | Path,
    out: str | Path | None = None,
    audio: str | Path | None = None,
    album_art: str | Path | None = None,
    track: int | None = None,
    tracks: str | None = None,
    lead_in_bars: int = chart_writer.DEFAULT_LEAD_IN_BARS,
    offset_ms: int = 0,
    charter: str = "",
    archive: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> ConvertResult:
    """Convert *gp_file* into a Clone Hero song folder.

    Programmatic equivalent of the `convert` CLI command, used by the GUI.
    Raises ConvertError on any user-facing failure.
    """
    def _info(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)

    path = Path(gp_file)
    suffix = path.suffix.lower()
    is_container = suffix in _CONTAINER_SUFFIXES

    if not path.exists():
        raise ConvertError(f"file not found: {path}")
    if not path.is_file():
        raise ConvertError(f"not a file: {path}")
    if suffix not in _ALL_SUFFIXES:
        supported = ", ".join(sorted(_ALL_SUFFIXES))
        raise ConvertError(f"unsupported file type '{path.suffix}' for {path.name} (supported: {supported})")

    try:
        xml_text = gpx_reader.extract_gpif(path) if is_container else None
    except gpx_reader.GpxFormatError as e:
        raise ConvertError(str(e)) from e

    if is_container and xml_text is not None:
        title, artist, credit = _get_gpx_metadata(xml_text, path)
        resolved_charter = charter or credit or "shred2chart"
        track_list = ir_gpif.list_tracks(xml_text)
    else:
        try:
            import guitarpro  # noqa: PLC0415
            song = guitarpro.parse(str(path))
        except Exception as e:
            raise ConvertError(
                f"error parsing {path}: {e}\n"
                "(legacy .gp3/.gp4/.gp5 support requires PyGuitarPro; "
                "run: pip install PyGuitarPro)"
            ) from e
        title = getattr(song, "title", "") or path.stem
        artist = getattr(song, "artist", "") or "Unknown Artist"
        resolved_charter = charter or "shred2chart"
        track_list = ir_gp.list_tracks(path)

    known = {t for t, _ in track_list}
    if tracks:
        try:
            track_ids = [int(t) for t in tracks.split(",")]
        except ValueError as e:
            raise ConvertError(f"tracks must be comma-separated numbers, got {tracks!r}") from e
        unknown = [t for t in track_ids if t not in known]
        if unknown:
            raise ConvertError(f"track(s) {unknown} not in this file")
    elif track is not None:
        if track not in known:
            raise ConvertError(f"track {track} not in this file")
        track_ids = [track]
    else:
        track_ids = _guess_guitar_tracks(track_list)

    names = dict(track_list)
    _info(f"{title} - {artist}")
    _info(f"blending tracks: {', '.join(f'{t} ({names[t]})' for t in track_ids)}")

    if is_container and xml_text is not None:
        tempo_events = gpif_tempo.dump_tempo_events(xml_text)
        sections = gpif_tempo.dump_sections(xml_text)
    else:
        try:
            tempo_events = tempo.dump_tempo_events(path)
        except Exception as e:
            raise ConvertError(f"error reading tempo from {path}: {e}") from e
        sections = []

    if is_container and xml_text is not None:
        bar_starts, _, _ = gpif_tempo.compute_bar_grid(ET.fromstring(xml_text))
    else:
        bar_starts = _estimate_bar_starts(tempo_events)

    blend_spans = sections
    if not blend_spans and len(track_ids) > 1:
        blend_spans = [
            {"tick": bar_starts[i], "bar": i, "name": f"bars {i + 1}-{min(i + 8, len(bar_starts))}"}
            for i in range(0, len(bar_starts), 8)
        ]
        _info("(no section markers in file - blending in 8-bar windows instead)")

    try:
        if is_container and xml_text is not None:
            tracks_notes = {t: ir_gpif.dump_ir(xml_text, track_index=t) for t in track_ids}
        else:
            tracks_notes = {t: ir_gp.dump_ir(path, track_index=t) for t in track_ids}
    except (gpx_reader.GpxFormatError, gpif_tempo.GpifFormatError, ValueError) as e:
        raise ConvertError(str(e)) from e
    except Exception as e:
        raise ConvertError(f"error parsing notes from {path}: {e}") from e

    blended, choices = blend.blend_tracks(tracks_notes, track_ids, blend_spans, bar_starts)
    section_ticks = [s["tick"] for s in sections]
    chart_notes = mapper.map_notes(blended, section_ticks=section_ticks)

    _info(f"{len(sections)} section(s), {len(blended)} notes after blending, {len(chart_notes)} chart events")
    for choice in choices:
        _info(f"  {choice['section']:<24} <- track {choice['track']} ({names[choice['track']]})")

    tempo_events, sections, chart_notes, lead_in_ms = chart_writer.add_lead_in(
        tempo_events,
        sections,
        chart_notes,
        bars=lead_in_bars,
    )
    effective_offset_ms = offset_ms - lead_in_ms
    if lead_in_ms:
        _info(f"added {lead_in_bars} empty lead-in bars before the chart")

    out_dir = Path(out) if out else _default_output_dir(artist, title)

    chart_writer.write_song_folder(
        out_dir, title, artist, tempo_events, sections, chart_notes,
        offset_ms=effective_offset_ms, charter=resolved_charter,
    )

    audio_source = None
    if audio:
        try:
            audio_source = Path(audio).expanduser()
            _prepare_audio(audio_source, out_dir)
        except (FileNotFoundError, RuntimeError) as e:
            raise ConvertError(str(e)) from e

    if album_art:
        art_source = Path(album_art).expanduser()
        if not art_source.is_file():
            raise ConvertError(f"album art file does not exist: {art_source}")
        if media.place_album_art(art_source, out_dir) is None:
            raise ConvertError(
                f"could not create album.png from {art_source}; "
                "install ffmpeg or choose a PNG image"
            )

    errors = validation.validate_song_folder(
        out_dir, title, artist, tempo_events, audio_required=bool(audio)
    )
    if errors:
        raise ConvertError("generated folder failed validation:\n" + "\n".join(f"  - {e}" for e in errors))

    manifest = integration.write_manifest(
        out_dir, title, artist, tempo_events, sections, len(chart_notes),
        offset_ms=effective_offset_ms, audio_path=audio_source,
    )

    if archive:
        _write_archive(out_dir, artist, title)
        _info(f"created archive: {out_dir.parent / (_safe_path_part(artist) + ' - ' + _safe_path_part(title))}.zip")

    _info(f"wrote {out_dir}/notes.chart, song.ini, and moon-scraper-manifest.json")
    if audio:
        _info(f"copied audio to {out_dir}/song.ogg")

    return ConvertResult(out_dir=out_dir, title=title, artist=artist, manifest=manifest)


def _cmd_convert(args: argparse.Namespace) -> int:
    _configure_logging(args)
    path = Path(args.gp_file)
    suffix = path.suffix.lower()
    is_container = suffix in _CONTAINER_SUFFIXES
    is_legacy = suffix in _LEGACY_SUFFIXES

    if _validate_input_file(path, _ALL_SUFFIXES):
        return 1

    # Keep the user-supplied charter value (may be "" if flag not given).
    args_charter = getattr(args, "charter", "") or ""
    quiet = getattr(args, "quiet", False)

    def _info(*msg: object) -> None:
        if not quiet and not args.json:
            print(*msg)

    try:
        if is_container:
            xml_text = gpx_reader.extract_gpif(path)
        else:
            xml_text = None
    except gpx_reader.GpxFormatError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Resolve metadata
    if is_container and xml_text is not None:
        title, artist, credit = _get_gpx_metadata(xml_text, path)
        # Priority: explicit --charter flag > GP SubTitle credit field > default
        charter = args_charter or credit or "shred2chart"
        tracks = ir_gpif.list_tracks(xml_text)
    else:
        # Legacy PyGuitarPro path: extract metadata from the parsed song
        try:
            import guitarpro  # noqa: PLC0415
            song = guitarpro.parse(str(path))
        except Exception as e:
            print(
                f"error parsing {path}: {e}\n"
                "(legacy .gp3/.gp4/.gp5 support requires PyGuitarPro; "
                "run: pip install PyGuitarPro)",
                file=sys.stderr,
            )
            return 1
        title = getattr(song, "title", "") or path.stem
        artist = getattr(song, "artist", "") or "Unknown Artist"
        charter = args_charter or "shred2chart"
        tracks = ir_gp.list_tracks(path)

    if args.tracks:
        try:
            track_ids = [int(t) for t in args.tracks.split(",")]
        except ValueError:
            print(f"error: --tracks must be comma-separated numbers, got {args.tracks!r}", file=sys.stderr)
            return 1
        known = {t for t, _ in tracks}
        unknown = [t for t in track_ids if t not in known]
        if unknown:
            print(f"error: track(s) {unknown} not in this file. Available:", file=sys.stderr)
            for track_id, name in tracks:
                print(f"  {track_id}: {name}", file=sys.stderr)
            return 1
    else:
        track_ids = _guess_guitar_tracks(tracks)

    names = dict(tracks)
    if args.interactive:
        interactive_options = _prompt_convert_options(args, tracks, track_ids, names, artist, title)
        if interactive_options is None:
            return 0
        track_ids, interactive_out = interactive_options
    else:
        interactive_out = None

    _info(f"{title} - {artist}")
    _info(f"blending tracks: {', '.join(f'{t} ({names[t]})' for t in track_ids)}")

    # Tempo/section events
    if is_container and xml_text is not None:
        tempo_events = gpif_tempo.dump_tempo_events(xml_text)
        sections = gpif_tempo.dump_sections(xml_text)
    else:
        try:
            tempo_events = tempo.dump_tempo_events(path)
        except Exception as e:
            print(f"error reading tempo from {path}: {e}", file=sys.stderr)
            return 1
        sections = []

    if is_container and xml_text is not None:
        bar_starts, _, _ = gpif_tempo.compute_bar_grid(ET.fromstring(xml_text))
    else:
        # Estimate bar grid from tempo events for legacy files
        bar_starts = _estimate_bar_starts(tempo_events)

    # Blend at section granularity; fall back to 8-bar windows for files
    # with no section markers.
    blend_spans = sections
    if not blend_spans and len(track_ids) > 1:
        blend_spans = [
            {"tick": bar_starts[i], "bar": i, "name": f"bars {i + 1}-{min(i + 8, len(bar_starts))}"}
            for i in range(0, len(bar_starts), 8)
        ]
        _info("(no section markers in file - blending in 8-bar windows instead)")

    # Per-track note extraction
    try:
        if is_container and xml_text is not None:
            tracks_notes = {t: ir_gpif.dump_ir(xml_text, track_index=t) for t in track_ids}
        else:
            tracks_notes = {t: ir_gp.dump_ir(path, track_index=t) for t in track_ids}
    except (gpx_reader.GpxFormatError, gpif_tempo.GpifFormatError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error parsing notes from {path}: {e}", file=sys.stderr)
        return 1

    blended, choices = blend.blend_tracks(tracks_notes, track_ids, blend_spans, bar_starts)
    section_ticks = [s["tick"] for s in sections]
    chart_notes = mapper.map_notes(blended, section_ticks=section_ticks)

    _info(
        f"\n{len(sections)} section(s), {len(blended)} notes after blending, "
        f"{len(chart_notes)} chart events:"
    )
    if not quiet and not args.json:
        for choice in choices:
            print(f"  {choice['section']:<24} <- track {choice['track']} ({names[choice['track']]})")

    tempo_events, sections, chart_notes, lead_in_ms = chart_writer.add_lead_in(
        tempo_events,
        sections,
        chart_notes,
        bars=args.lead_in_bars,
    )
    effective_offset_ms = args.offset_ms - lead_in_ms
    if lead_in_ms:
        _info(f"added {args.lead_in_bars} empty lead-in bars before the chart")

    if args.dry_run:
        if interactive_out is not None:
            out_dir = interactive_out
        elif args.out:
            out_dir = Path(args.out)
        else:
            out_dir = _default_output_dir(artist, title)
        print(f"\n[dry-run] would write: {out_dir}/notes.chart, song.ini")
        print("[dry-run] no files written.")
        return 0

    if interactive_out is not None:
        out_dir = interactive_out
    elif args.out:
        out_dir = Path(args.out)
    else:
        out_dir = _default_output_dir(artist, title)

    chart_writer.write_song_folder(
        out_dir, title, artist, tempo_events, sections, chart_notes,
        offset_ms=effective_offset_ms, charter=charter,
    )
    audio_source = None
    if args.audio:
        try:
            audio_source = Path(args.audio).expanduser()
            _prepare_audio(audio_source, out_dir)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    errors = validation.validate_song_folder(
        out_dir, title, artist, tempo_events, audio_required=bool(args.audio)
    )
    if errors:
        print("error: generated folder failed validation:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    manifest = integration.write_manifest(
        out_dir, title, artist, tempo_events, sections, len(chart_notes),
        offset_ms=effective_offset_ms, audio_path=audio_source,
    )

    if getattr(args, "archive", False):
        _write_archive(out_dir, artist, title)
        _info(f"created archive: {out_dir.parent / (_safe_path_part(artist) + ' - ' + _safe_path_part(title))}.zip")

    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        _info(f"\nwrote {out_dir}/notes.chart, song.ini, and moon-scraper-manifest.json")
        if args.audio:
            _info(f"copied audio to {out_dir}/song.ogg")
        else:
            _print_audio_reminder(out_dir)
        _info("Drop the folder into Clone Hero's Songs directory (or open notes.chart in Moon Scraper).")
    return 0


def _estimate_bar_starts(tempo_events: list[dict]) -> list[int]:
    """Minimal bar-grid estimate for legacy files without a GPIF bar structure.

    Uses only the tempo events to produce approximate bar start ticks so
    8-bar blend-window fallback works even for .gp3/.gp4/.gp5 files.
    Assumes 4/4 throughout (a rough but usable default).
    """
    from .mapper import IR_TICKS_PER_QUARTER  # noqa: PLC0415
    ticks_per_bar = IR_TICKS_PER_QUARTER * 4
    # Conservative upper bound: last tempo-event tick + enough bars to cover
    # a typical song length.  The bar grid is only used for blend windows, so
    # over-estimating is harmless.
    _FALLBACK_BAR_COUNT = 200  # well above any realistic song length
    max_tick = max((e["tick"] for e in tempo_events), default=0) + ticks_per_bar * _FALLBACK_BAR_COUNT
    return list(range(0, max_tick, ticks_per_bar))


def _write_archive(out_dir: Path, artist: str, title: str) -> None:
    """Zip notes.chart, song.ini, and song.ogg (if present) alongside the folder."""
    archive_name = f"{_safe_path_part(artist)} - {_safe_path_part(title)}.zip"
    archive_path = out_dir.parent / archive_name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        folder_name = out_dir.name
        for filename in ("notes.chart", "song.ini", "song.ogg"):
            file_path = out_dir / filename
            if file_path.is_file():
                zf.write(file_path, f"{folder_name}/{filename}")


def _print_audio_reminder(out_dir: Path) -> None:
    print(
        "\nAudio not provided — before playing in Clone Hero, add song.ogg:\n"
        f"  1. Place your audio in the song folder: {out_dir}/\n"
        "  2. Convert to OGG (if not already):\n"
        "       ffmpeg -i song.flac -q:a 6 song.ogg\n"
        "  3. Drop the whole folder into Clone Hero's Songs directory."
    )
    if shutil.which("ffmpeg") is None:
        print(f"\n  ffmpeg not found on PATH.\n{_ffmpeg_install_hint()}")


def _cmd_check(args: argparse.Namespace) -> int:
    out_dir = Path(args.song_dir)
    if not out_dir.exists():
        print(f"error: directory not found: {out_dir}", file=sys.stderr)
        return 1
    if not out_dir.is_dir():
        print(f"error: not a directory: {out_dir}", file=sys.stderr)
        return 1

    # Read song.ini to get name and artist for validation.
    song_ini = out_dir / "song.ini"
    parser = configparser.ConfigParser()
    parser.read(song_ini, encoding="utf-8")
    title = parser.get("song", "name", fallback="")
    artist = parser.get("song", "artist", fallback="")

    errors = validation.validate_song_folder(
        out_dir, title, artist,
        tempo_events=[{"tick": 0, "type": "tempo", "bpm": 120}],  # non-empty sentinel
        audio_required=args.require_audio,
    )
    if errors:
        print(f"validation failed for {out_dir}:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"OK: {out_dir}")
    return 0


def _cmd_moon_scraper(args: argparse.Namespace) -> int:
    try:
        result = integration.invoke_moon_scraper(args.manifest, args.command, args.timeout)
    except (FileNotFoundError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: Moon Scraper integration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
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

    if _validate_input_file(gpx_path, _CONTAINER_SUFFIXES):
        return 1
    if _validate_input_file(gp_path, _LEGACY_SUFFIXES):
        return 1

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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="enable debug-level logging",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress all progress output; emit only errors to stderr",
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
        help="convert a .gp/.gpx/.gp3/.gp4/.gp5 file into a Clone Hero song folder",
    )
    p_convert.add_argument("gp_file", help="path to a .gp (GP7/8), .gpx (GP6), or .gp3/.gp4/.gp5 file")
    p_convert.add_argument(
        "--tracks",
        help="comma-separated track numbers to blend, in priority order (see `list-tracks`); "
        "default: every guitar-named track, blended per section",
    )
    p_convert.add_argument("-o", "--out", help="output folder (default: songs/Artist - Title)")
    p_convert.add_argument(
        "--offset-ms", type=int, default=0,
        help="fine audio offset in milliseconds, applied after the lead-in (default 0)",
    )
    p_convert.add_argument(
        "--lead-in-bars",
        type=int,
        default=chart_writer.DEFAULT_LEAD_IN_BARS,
        help="empty measures before the score starts "
        f"(default {chart_writer.DEFAULT_LEAD_IN_BARS}; 0 disables)",
    )
    p_convert.add_argument(
        "-i", "--interactive", action="store_true",
        help="review tracks and output folder interactively before writing",
    )
    p_convert.add_argument(
        "--audio", help="audio input; copied as song.ogg or converted with ffmpeg",
    )
    p_convert.add_argument(
        "--json", action="store_true",
        help="emit a versioned machine-readable Moon Scraper manifest",
    )
    p_convert.add_argument(
        "--dry-run", action="store_true",
        help="show what would be done without writing any files",
    )
    p_convert.add_argument(
        "--charter", default="",
        help=(
            "charter name written into song.ini and notes.chart. "
            "For GP7/8 files, the GP SubTitle field is used as a fallback "
            "when this flag is not set. Final default: 'shred2chart'."
        ),
    )
    p_convert.add_argument(
        "--archive", action="store_true",
        help="create a ready-to-import ZIP archive alongside the song folder",
    )
    p_convert.set_defaults(func=_cmd_convert)

    p_check = sub.add_parser(
        "check",
        help="validate a generated song folder (notes.chart + song.ini) without Moon Scraper",
    )
    p_check.add_argument("song_dir", help="path to the generated song folder to validate")
    p_check.add_argument(
        "--require-audio", action="store_true",
        help="also check that song.ogg is present",
    )
    p_check.set_defaults(func=_cmd_check)

    p_moon = sub.add_parser(
        "moon-scraper", help="send a manifest to a custom Moon Scraper fork",
    )
    p_moon.add_argument("manifest", help="path to moon-scraper-manifest.json")
    p_moon.add_argument(
        "--command", required=True,
        help="fork command; manifest JSON is sent to its standard input",
    )
    p_moon.add_argument("--timeout", type=int, default=300)
    p_moon.set_defaults(func=_cmd_moon_scraper)

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
    if _ARGCOMPLETE_AVAILABLE:
        argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    _configure_logging(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
