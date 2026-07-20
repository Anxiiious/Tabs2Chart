from argparse import Namespace

from shred2chart.cli import _prompt_convert_options, build_parser


def test_convert_parser_supports_interactive_mode():
    args = build_parser().parse_args(["convert", "song.gp", "--interactive"])

    assert args.interactive is True


def test_interactive_prompt_allows_custom_track_order_and_output_selection(monkeypatch, tmp_path):
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


def test_interactive_prompt_retries_invalid_track_selection(monkeypatch, tmp_path, capsys):
    answers = iter(["bad", "9", "1", str(tmp_path / "output")])
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    args = Namespace(out=None)

    result = _prompt_convert_options(
        args,
        [(0, "Rhythm Guitar"), (1, "Lead Guitar")],
        [1, 0],
        {0: "Rhythm Guitar", 1: "Lead Guitar"},
        "Artist",
        "Title",
    )

    assert result == ([1], tmp_path / "output")
    assert len(prompts) == 4
    output = capsys.readouterr().out
    assert "Please enter comma-separated track numbers." in output
    assert "Unknown track(s): 9" in output


def test_interactive_prompt_accepts_default_tracks(monkeypatch, tmp_path):
    answers = iter(["", str(tmp_path / "output")])
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


def test_interactive_prompt_can_cancel_overwrite(monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "existing.chart").write_text("old", encoding="utf-8")
    answers = iter(["1", str(output), "n"])
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

    assert result is None
