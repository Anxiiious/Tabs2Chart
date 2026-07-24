from pathlib import Path

from shred2chart import gui


def test_parse_dnd_path_accepts_braced_path_with_spaces():
    assert gui._parse_dnd_path(r"{C:\Music Files\song.gp}") == r"C:\Music Files\song.gp"


def test_parse_dnd_path_uses_first_of_multiple_files():
    assert gui._parse_dnd_path(r"{C:\Tabs\one song.gp} C:\Tabs\two.gp") == (
        r"C:\Tabs\one song.gp"
    )


def test_suggest_companion_files_prefers_same_stem(tmp_path):
    tab = tmp_path / "My Song.gp"
    audio = tmp_path / "My Song.flac"
    art = tmp_path / "My Song.jpg"
    tab.write_bytes(b"tab")
    audio.write_bytes(b"audio")
    art.write_bytes(b"art")

    assert gui._suggest_companion_files(tab) == (audio, art)


def test_suggest_companion_files_finds_named_cover(tmp_path):
    tab = tmp_path / "My Song.gp5"
    cover = tmp_path / "cover.png"
    tab.write_bytes(b"tab")
    cover.write_bytes(b"art")

    assert gui._suggest_companion_files(tab) == (None, cover)


def test_song_output_dir_sanitizes_metadata(tmp_path):
    result = gui._song_output_dir(tmp_path, "Artist/Name", 'Title: "Live"')

    assert result.parent == tmp_path
    assert result.name == "Artist_Name - Title_ _Live_"


def test_gui_picker_supports_all_converter_formats():
    pattern = gui._GP_FILETYPES[0][1]
    for suffix in gui._GP_SUFFIXES:
        assert f"*{suffix}" in pattern
