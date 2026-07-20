"""Audio and album-art handling for a song folder (ffmpeg-backed, optional).

Both helpers shell out to an `ffmpeg` binary - either on PATH, or a
portable build unzipped into an `ffmpeg/bin/` folder next to the repo
(see .gitignore: that folder is a local, non-committed convenience, not
part of the project). Neither helper raises when ffmpeg is missing or a
conversion fails - `convert` treats audio/art as a nice-to-have, not
something that should abort a chart that otherwise came out fine.
Callers check the return value (None on skip/failure) and print their
own message.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

AUDIO_EXTENSIONS = {".ogg", ".mp3", ".wav", ".flac", ".m4a", ".opus", ".wma", ".aac"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_CANDIDATES = [
    _REPO_ROOT / "ffmpeg" / "bin" / "ffmpeg.exe",
    _REPO_ROOT / "ffmpeg" / "bin" / "ffmpeg",
]


def find_ffmpeg() -> str | None:
    """Locate an ffmpeg binary: PATH first, then a bundled ffmpeg/bin/ next
    to the repo. Returns the path/command to invoke, or None if neither exists."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    for candidate in _BUNDLED_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def convert_audio(src: str | Path, out_dir: str | Path) -> Path | None:
    """Convert an audio file to song.ogg inside out_dir via ffmpeg.

    Returns the written path, or None if ffmpeg is unavailable or the
    conversion failed. A source that's already a .ogg is still passed
    through ffmpeg (cheap re-encode) so callers get one guaranteed format.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None

    src_path = Path(src)
    out_path = Path(out_dir) / "song.ogg"
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(src_path), "-vn", "-c:a", "libvorbis", "-q:a", "6", str(out_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        return None
    return out_path


def place_album_art(src: str | Path, out_dir: str | Path) -> Path | None:
    """Place album art as album.png inside out_dir.

    Already-PNG sources are copied directly; anything else ffmpeg can
    decode is converted - including an audio file whose embedded cover
    art (FLAC/MP3 attached_pic) should be pulled out as the album art.
    -frames:v 1 -update 1 is required for that case: ffmpeg's image2
    muxer otherwise expects a sequence-pattern filename and refuses to
    write a single still frame from a stream. Returns the written path,
    or None if the source can't be placed.
    """
    src_path = Path(src)
    out_path = Path(out_dir) / "album.png"

    if src_path.suffix.lower() == ".png":
        shutil.copyfile(src_path, out_path)
        return out_path

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None

    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(src_path), "-frames:v", "1", "-update", "1", str(out_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        return None
    return out_path
