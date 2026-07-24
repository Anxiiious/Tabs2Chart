from pathlib import Path

import pytest

from shred2chart import moonscraper


def test_find_moonscraper_prefers_valid_configured_path(tmp_path, monkeypatch):
    executable = tmp_path / "Custom MoonScraper.exe"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(moonscraper, "_registry_candidates", lambda: ())
    monkeypatch.setattr(moonscraper, "_common_candidates", lambda: ())

    assert moonscraper.find_moonscraper(executable) == executable.resolve()


def test_find_moonscraper_falls_back_when_configured_path_is_stale(tmp_path, monkeypatch):
    discovered = tmp_path / "Moonscraper Chart Editor.exe"
    discovered.write_bytes(b"exe")
    monkeypatch.setattr(moonscraper, "_registry_candidates", lambda: (discovered,))
    monkeypatch.setattr(moonscraper, "_common_candidates", lambda: ())

    assert moonscraper.find_moonscraper(tmp_path / "missing.exe") == discovered.resolve()


def test_find_moonscraper_prefers_bundled_candidate_over_registry(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.exe"
    installed = tmp_path / "installed.exe"
    bundled.write_bytes(b"exe")
    installed.write_bytes(b"exe")
    monkeypatch.setattr(moonscraper, "_common_candidates", lambda: (bundled,))
    monkeypatch.setattr(moonscraper, "_registry_candidates", lambda: (installed,))

    assert moonscraper.find_moonscraper() == bundled.resolve()


def test_open_chart_passes_chart_as_argument_and_uses_song_folder(tmp_path):
    executable = tmp_path / "Moonscraper Chart Editor.exe"
    song_dir = tmp_path / "Artist - Song"
    chart = song_dir / "notes.chart"
    executable.write_bytes(b"exe")
    song_dir.mkdir()
    chart.write_text("[Song]\n", encoding="utf-8")
    called = {}

    def fake_popen(args, cwd):
        called["args"] = args
        called["cwd"] = cwd
        return object()

    moonscraper.open_chart(chart, executable, popen=fake_popen)

    assert called["args"] == [str(executable.resolve()), str(chart.resolve())]
    assert called["cwd"] == str(song_dir.resolve())


def test_open_chart_passes_manifest_to_custom_build_with_stock_chart_fallback(tmp_path):
    executable = tmp_path / "Moonscraper Chart Editor.exe"
    chart = tmp_path / "notes.chart"
    manifest = tmp_path / "moon-scraper-manifest.json"
    executable.write_bytes(b"exe")
    chart.write_text("[Song]\n", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    called = {}

    def fake_popen(args, cwd):
        called["args"] = args
        return object()

    moonscraper.open_chart(chart, executable, manifest_path=manifest, popen=fake_popen)

    assert called["args"] == [
        str(executable.resolve()),
        "--tabs2chart-manifest",
        str(manifest.resolve()),
        str(chart.resolve()),
    ]


@pytest.mark.parametrize("missing", ["chart", "executable"])
def test_open_chart_rejects_missing_inputs(tmp_path, missing):
    executable = tmp_path / "Moonscraper Chart Editor.exe"
    chart = tmp_path / "notes.chart"
    executable.write_bytes(b"exe")
    chart.write_text("[Song]\n", encoding="utf-8")
    if missing == "chart":
        chart.unlink()
    else:
        executable.unlink()

    with pytest.raises(moonscraper.MoonscraperLaunchError):
        moonscraper.open_chart(chart, executable)
