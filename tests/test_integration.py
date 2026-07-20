import json
import sys

from shred2chart.cli import _prepare_audio
from shred2chart.integration import invoke_moon_scraper, write_manifest
from shred2chart.validation import validate_song_folder


def test_manifest_has_stable_paths_and_timing(tmp_path):
    out = tmp_path / "song"
    out.mkdir()
    manifest = write_manifest(
        out, "Title", "Artist",
        [{"tick": 0, "type": "tempo", "bpm": 120}],
        [{"tick": 0, "bar": 0, "name": "Intro"}],
        chart_notes=3,
        offset_ms=25,
    )

    saved = json.loads((out / "moon-scraper-manifest.json").read_text())
    assert saved["schema"].endswith("moon-scraper-manifest.v1.json")
    assert saved["output"]["chart"] == str(out.resolve() / "notes.chart")
    assert manifest["timing"]["offset_ms"] == 25


def test_mock_moon_scraper_receives_manifest_on_stdin(tmp_path):
    out = tmp_path / "song"
    out.mkdir()
    write_manifest(out, "Title", "Artist", [], [], chart_notes=0)
    manifest_path = out / "moon-scraper-manifest.json"
    command = (
        f"{sys.executable} -c "
        "'import json,sys; data=json.load(sys.stdin); "
        "print(data[\"version\"])'"
    )

    result = invoke_moon_scraper(manifest_path, command)

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "1"


def test_prepare_audio_copies_ogg(tmp_path):
    source = tmp_path / "input.ogg"
    source.write_bytes(b"audio")
    out = tmp_path / "song"
    out.mkdir()

    target = _prepare_audio(source, out)

    assert target == out / "song.ogg"
    assert target.read_bytes() == b"audio"


def test_prepare_audio_uses_ffmpeg_for_other_formats(tmp_path, monkeypatch):
    source = tmp_path / "input.wav"
    source.write_bytes(b"audio")
    out = tmp_path / "song"
    out.mkdir()
    calls = []

    monkeypatch.setattr("shred2chart.cli.shutil.which", lambda _: "/usr/bin/ffmpeg")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        (out / "song.ogg").write_bytes(b"converted")

    monkeypatch.setattr("shred2chart.cli.subprocess.run", fake_run)
    target = _prepare_audio(source, out)

    assert target.is_file()
    command = calls[0][0]
    assert command[0] == "/usr/bin/ffmpeg"
    assert command[command.index("-acodec") + 1] == "libvorbis"
    assert command[-1] == str(out / "song.ogg")


def test_song_folder_validation_catches_missing_audio(tmp_path):
    out = tmp_path / "song"
    out.mkdir()
    # Include [ExpertSingle] with at least one note so the only failure is the missing audio.
    (out / "notes.chart").write_text(
        "[Song]\n{\n}\n\n[ExpertSingle]\n{\n  0 = N 0 0\n}\n", encoding="utf-8"
    )
    (out / "song.ini").write_text(
        "[song]\nname = Title\nartist = Artist\n", encoding="utf-8"
    )

    errors = validate_song_folder(
        out, "Title", "Artist", [{"tick": 0, "type": "tempo", "bpm": 120}],
        audio_required=True,
    )

    assert errors == [f"missing audio: {out / 'song.ogg'}"]
