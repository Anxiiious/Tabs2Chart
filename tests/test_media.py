"""Tests for the ffmpeg-backed audio/album-art helpers.

subprocess.run and shutil.which are monkeypatched throughout so these
tests don't require ffmpeg to actually be installed - they check that
media.py builds the right commands and handles success/failure/missing-
ffmpeg correctly, not that ffmpeg itself works.

Every test that wants "no ffmpeg available" must also blank out
media._BUNDLED_CANDIDATES: this repo may have a real ffmpeg/bin/ folder
checked out locally (see .gitignore), and find_ffmpeg() falls back to
it when shutil.which() misses - without blanking it, "missing ffmpeg"
tests would silently pick up that real, on-disk binary.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shred2chart import media


@pytest.fixture(autouse=True)
def _no_bundled_ffmpeg(monkeypatch):
    """Prevent tests from picking up a real, locally-checked-out ffmpeg/bin/
    binary - tests that want the bundled fallback opt back in explicitly."""
    monkeypatch.setattr(media, "_BUNDLED_CANDIDATES", [])


def _fake_run_factory(create_output=True):
    """Build a fake subprocess.run that writes an empty file at the -y ... <out>
    tail argument (mimicking a successful ffmpeg run) and returns rc 0."""

    def _fake_run(cmd, capture_output=True, text=True):
        if create_output:
            Path(cmd[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return _fake_run


def test_ffmpeg_available_true(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert media.ffmpeg_available() is True


def test_ffmpeg_available_false(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.ffmpeg_available() is False


def test_convert_audio_missing_ffmpeg_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    src = tmp_path / "song.flac"
    src.write_bytes(b"fake flac data")
    result = media.convert_audio(src, tmp_path)
    assert result is None
    assert not (tmp_path / "song.ogg").exists()


def test_convert_audio_success(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", _fake_run_factory())
    src = tmp_path / "song.flac"
    src.write_bytes(b"fake flac data")

    result = media.convert_audio(src, tmp_path)

    assert result == tmp_path / "song.ogg"
    assert result.exists()


def test_convert_audio_ffmpeg_failure_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        media.subprocess, "run",
        lambda cmd, capture_output=True, text=True: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    src = tmp_path / "song.flac"
    src.write_bytes(b"fake flac data")

    result = media.convert_audio(src, tmp_path)

    assert result is None
    assert not (tmp_path / "song.ogg").exists()


def test_convert_audio_command_shape(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", _fake_run)
    src = tmp_path / "song.wav"
    src.write_bytes(b"fake wav data")

    media.convert_audio(src, tmp_path)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert str(src) in cmd
    assert cmd[-1] == str(tmp_path / "song.ogg")
    assert "-c:a" in cmd and "libvorbis" in cmd


def test_place_album_art_png_is_copied_not_converted(monkeypatch, tmp_path):
    def _fail_if_called(cmd, capture_output=True, text=True):
        raise AssertionError("ffmpeg should not be invoked for a .png source")

    monkeypatch.setattr(media.subprocess, "run", _fail_if_called)
    src = tmp_path / "cover.png"
    src.write_bytes(b"fake png bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = media.place_album_art(src, out_dir)

    assert result == out_dir / "album.png"
    assert result.read_bytes() == b"fake png bytes"


def test_place_album_art_converts_non_png(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", _fake_run_factory())
    src = tmp_path / "cover.jpg"
    src.write_bytes(b"fake jpg bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = media.place_album_art(src, out_dir)

    assert result == out_dir / "album.png"
    assert result.exists()


def test_place_album_art_command_has_single_frame_flags(monkeypatch, tmp_path):
    """Regression test: without -frames:v 1 -update 1, ffmpeg's image2 muxer
    refuses to write a still frame extracted from a stream (e.g. a FLAC's
    embedded attached_pic cover art) - see media.place_album_art docstring."""
    captured = {}

    def _fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", _fake_run)
    src = tmp_path / "song.flac"
    src.write_bytes(b"fake flac bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    media.place_album_art(src, out_dir)

    cmd = captured["cmd"]
    assert "-frames:v" in cmd and "1" in cmd
    assert "-update" in cmd


def test_place_album_art_missing_ffmpeg_for_non_png_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    src = tmp_path / "cover.jpg"
    src.write_bytes(b"fake jpg bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = media.place_album_art(src, out_dir)

    assert result is None
    assert not (out_dir / "album.png").exists()


def test_place_album_art_ffmpeg_failure_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        media.subprocess, "run",
        lambda cmd, capture_output=True, text=True: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    src = tmp_path / "cover.jpg"
    src.write_bytes(b"fake jpg bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = media.place_album_art(src, out_dir)

    assert result is None


def test_find_ffmpeg_prefers_path_over_bundled(monkeypatch, tmp_path):
    bundled = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    monkeypatch.setattr(media, "_BUNDLED_CANDIDATES", [bundled])
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert media.find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_falls_back_to_bundled(monkeypatch, tmp_path):
    bundled = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    monkeypatch.setattr(media, "_BUNDLED_CANDIDATES", [bundled])
    monkeypatch.setattr(media.shutil, "which", lambda name: None)

    assert media.find_ffmpeg() == str(bundled)


def test_find_ffmpeg_none_when_neither_present(monkeypatch, tmp_path):
    bundled = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"  # never created
    monkeypatch.setattr(media, "_BUNDLED_CANDIDATES", [bundled])
    monkeypatch.setattr(media.shutil, "which", lambda name: None)

    assert media.find_ffmpeg() is None
