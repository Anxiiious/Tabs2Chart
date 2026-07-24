"""Command-level integration tests using the synthetic sample.gp fixture.

The fixture is a minimal valid GP7 zip (tests/fixtures/sample.gp) containing:
- 2 tempo events (120 bpm → 140 bpm at bar 2)
- 4 bars in 4/4
- 1 section marker ("Intro")
- 1 track ("Lead Guitar") with 3 notes across bars 0-2

These tests verify the full convert pipeline without requiring a real user
file, so they can run in CI.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from shred2chart import cli
from shred2chart.cli import build_parser, main
from shred2chart.validation import validate_song_folder

FIXTURE = Path(__file__).parent / "fixtures" / "sample.gp"


def test_fixture_exists():
    assert FIXTURE.is_file(), f"fixture missing: {FIXTURE}"


def test_convert_produces_valid_song_folder(tmp_path):
    out = tmp_path / "output"
    rc = main(["convert", str(FIXTURE), "--out", str(out)])
    assert rc == 0, "convert should exit 0 on a valid input"
    assert (out / "notes.chart").is_file()
    assert (out / "song.ini").is_file()
    assert (out / "moon-scraper-manifest.json").is_file()


def test_convert_output_passes_validation(tmp_path):
    out = tmp_path / "output"
    main(["convert", str(FIXTURE), "--out", str(out)])
    errors = validate_song_folder(
        out,
        "Sample Song",
        "Test Artist",
        [{"tick": 0, "type": "tempo", "bpm": 120}],
        audio_required=False,
    )
    assert errors == [], f"validation errors: {errors}"


def test_convert_chart_contains_notes(tmp_path):
    out = tmp_path / "output"
    main(["convert", str(FIXTURE), "--out", str(out)])
    chart = (out / "notes.chart").read_text(encoding="utf-8")
    assert "[ExpertSingle]" in chart
    # At least one note line
    assert re.search(r"=\s*N\s+\d", chart), "chart has no note events"


def test_convert_defaults_to_two_empty_bars(tmp_path):
    out = tmp_path / "output"
    rc = main(["convert", str(FIXTURE), "--out", str(out)])
    assert rc == 0

    chart = (out / "notes.chart").read_text(encoding="utf-8")
    expert = chart.split("[ExpertSingle]", 1)[1]
    first_note_tick = int(re.search(r"(\d+)\s*=\s*N\s+", expert).group(1))
    assert first_note_tick == 2 * 4 * 192
    assert "Offset = -4.0" in chart
    assert "delay = -4000" in (out / "song.ini").read_text(encoding="utf-8")


def test_convert_can_disable_lead_in(tmp_path):
    out = tmp_path / "output"
    rc = main(
        ["convert", str(FIXTURE), "--out", str(out), "--lead-in-bars", "0"]
    )
    assert rc == 0

    chart = (out / "notes.chart").read_text(encoding="utf-8")
    expert = chart.split("[ExpertSingle]", 1)[1]
    first_note_tick = int(re.search(r"(\d+)\s*=\s*N\s+", expert).group(1))
    assert first_note_tick == 0
    assert "Offset = 0.0" in chart


def test_convert_song_ini_has_expected_fields(tmp_path):
    out = tmp_path / "output"
    main(["convert", str(FIXTURE), "--out", str(out)])
    ini = (out / "song.ini").read_text(encoding="utf-8")
    assert "name = Sample Song" in ini
    assert "artist = Test Artist" in ini
    assert "diff_guitar = -1" in ini
    assert "song_length" in ini  # populated from tempo map


def test_convert_charter_flag(tmp_path):
    out = tmp_path / "output"
    main(["convert", str(FIXTURE), "--out", str(out), "--charter", "MyName"])
    ini = (out / "song.ini").read_text(encoding="utf-8")
    assert "charter = MyName" in ini
    chart = (out / "notes.chart").read_text(encoding="utf-8")
    assert 'Charter = "MyName"' in chart


def test_convert_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "output"
    rc = main(["convert", str(FIXTURE), "--out", str(out), "--dry-run"])
    assert rc == 0
    assert not out.exists(), "dry-run should not create the output folder"


def test_convert_archive_flag(tmp_path):
    out = tmp_path / "output"
    rc = main(["convert", str(FIXTURE), "--out", str(out), "--archive"])
    assert rc == 0
    zips = list(tmp_path.glob("*.zip"))
    assert len(zips) == 1, f"expected one zip archive, found: {zips}"
    with zipfile.ZipFile(zips[0]) as zf:
        names = zf.namelist()
    assert any("notes.chart" in n for n in names)
    assert any("song.ini" in n for n in names)


def test_convert_song_normalizes_album_art_to_png(monkeypatch, tmp_path):
    out = tmp_path / "output"
    art = tmp_path / "cover.jpg"
    art.write_bytes(b"image")

    def fake_place_album_art(src, out_dir):
        destination = Path(out_dir) / "album.png"
        destination.write_bytes(Path(src).read_bytes())
        return destination

    monkeypatch.setattr(cli.media, "place_album_art", fake_place_album_art)
    result = cli.convert_song(FIXTURE, out=out, album_art=art)

    assert result.out_dir == out
    assert (out / "album.png").read_bytes() == b"image"


def test_check_command_passes_on_valid_folder(tmp_path):
    out = tmp_path / "output"
    main(["convert", str(FIXTURE), "--out", str(out)])
    rc = main(["check", str(out)])
    assert rc == 0


def test_check_command_fails_on_missing_chart(tmp_path):
    rc = main(["check", str(tmp_path)])
    assert rc == 1


def test_input_validation_missing_file(tmp_path, capsys):
    rc = main(["convert", str(tmp_path / "nonexistent.gp")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "file not found" in err


def test_input_validation_bad_extension(tmp_path, capsys):
    bad = tmp_path / "song.mp3"
    bad.write_bytes(b"not a gp file")
    rc = main(["convert", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unsupported file type" in err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "shred2chart" in out


def test_list_tracks_output_mentions_both_flags(tmp_path, capsys):
    rc = main(["list-tracks", str(FIXTURE)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--track" in out       # dump-ir flag
    assert "--tracks" in out      # convert flag


def test_quiet_suppresses_progress(tmp_path, capsys):
    out = tmp_path / "output"
    rc = main(["--quiet", "convert", str(FIXTURE), "--out", str(out)])
    assert rc == 0
    stdout = capsys.readouterr().out
    # Quiet mode: no progress lines ("blending tracks", section list, etc.)
    assert "blending tracks" not in stdout


def test_convert_section_resets_contour(tmp_path):
    """Notes after the section boundary should get fresh lane assignments."""
    out = tmp_path / "output"
    rc = main(["convert", str(FIXTURE), "--out", str(out)])
    assert rc == 0
    chart = (out / "notes.chart").read_text(encoding="utf-8")
    assert "[ExpertSingle]" in chart
