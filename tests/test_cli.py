from argparse import Namespace

from shred2chart.cli import _prompt_convert_options, build_parser


def test_convert_parser_supports_interactive_mode():
    args = build_parser().parse_args(["convert", "song.gp", "--interactive"])

    assert args.interactive is True


def test_interactive_prompt_allows_track_and_output_selection(monkeypatch, tmp_path):
    answers = iter(["1,0", str(tmp_path / "output")])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    args = Namespace(out=None)

    result = _prompt_convert_options(
        args,
        [(0, "Rhythm Guitar"), (1, "Lead Guitar")],
        [1, 0],
        {0: "Rhythm Guitar", 1: "Lead Guitar"},
        "Artist",
        "Title",
    )

    assert result == ([1, 0], tmp_path / "output")
