import json
import sys

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
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"version": 1, "output": {"chart": "notes.chart"}}')
    command = (
        f"{sys.executable} -c "
        "'import json,sys; data=json.load(sys.stdin); "
        "print(data[\"version\"])'"
    )

    result = invoke_moon_scraper(manifest_path, command)

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "1"


def test_song_folder_validation_catches_missing_audio(tmp_path):
    out = tmp_path / "song"
    out.mkdir()
    (out / "notes.chart").write_text("[Song]\n{\n}\n")
    (out / "song.ini").write_text(
        "[song]\nname = Title\nartist = Artist\n", encoding="utf-8"
    )

    errors = validate_song_folder(
        out, "Title", "Artist", [{"tick": 0, "type": "tempo", "bpm": 120}],
        audio_required=True,
    )

    assert errors == [f"missing audio: {out / 'song.ogg'}"]
