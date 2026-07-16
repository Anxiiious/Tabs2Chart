# Tabs2Chart (shred2chart)

Converts Sheet Happens Guitar Pro tabs (`.gpx`) into Clone Hero charts (`.chart` + `song.ini`),
preserving the tab's tempo so the chart is rhythm-synced to the real recording.

**Full plan, current progress, and open decisions live in [`SHRED2CHART_GAMEPLAN.md`](SHRED2CHART_GAMEPLAN.md).**
That file is the project's source of truth — read it before diving deeper than this README.

**Status:** early scaffolding. Not yet able to produce a finished chart — see "Where things stand" below.

This README assumes you've never used Python packaging, `pytest`, or GitHub before. Every command
below is meant to be copy-pasted as-is.

---

## 1. One-time setup

You need Python 3.10 or newer and `git`. Check what you have:

```bash
python3 --version
git --version
```

Then, from inside this repo folder, install the project (this also installs its one dependency,
`PyGuitarPro`):

```bash
pip install -e ".[dev]"
```

`-e` means "editable" — if you or an agent edits the code afterward, you don't need to reinstall.

## 2. Check it actually works

```bash
pytest
```

You should see all tests pass (`N passed`). If anything fails, something is broken — don't trust
the tool until this is green again.

## 3. What you can run today

Three commands exist so far, run as `shred2chart <command>`:

| Command | What it does |
|---|---|
| `shred2chart dump-gpif song.gpx` | Pulls the raw `score.gpif` XML out of a `.gpx` file and saves it next to it. |
| `shred2chart dump-tempo song.gp5` | Prints every tempo/time-signature change found in a `.gp3`/`.gp4`/`.gp5` file, as JSON. |
| `shred2chart verify-m0 song.gpx song.gp5` | Runs both of the above on the *same song* and tells you what to compare by eye. This is milestone **M0** from the game plan. |

None of these produce a playable Clone Hero chart yet — that's a later milestone (M3/M4 in the
game plan).

## 4. Your next concrete step: run the M0 check

This is the very first thing the game plan asks for, and it needs something only you have: an
actual Sheet Happens `.gpx` file.

1. Pick one song's `.gpx` file. Put it somewhere handy, e.g. this repo's `test_data/` folder
   (that folder is git-ignored, so it's fine to drop real files there — they won't get committed).
2. Convert that same file to `.gp5` using [TuxGuitar](https://sourceforge.net/projects/tuxguitar/)
   (free) or Guitar Pro if you own it: open the `.gpx`, then "Save As" / "Export" → Guitar Pro 5
   format. Save it next to the original, e.g. `test_data/song.gp5`.
3. Run:
   ```bash
   shred2chart verify-m0 test_data/song.gpx test_data/song.gp5
   ```
4. It prints the tempo changes it found, extracts `score.gpif`, and tells you exactly what to
   compare (the tempo track shown in Guitar Pro/TuxGuitar for the original `.gpx`, versus the
   numbers printed). If they match — same bar positions, same BPM values, nothing missing — that's
   a **GO** on the project's chosen approach ("Route A" in the game plan). If they don't match,
   that's a **NO-GO**, and the game plan has a documented fallback plan ("Route B").

Whatever you find, tell your coding agent (Claude Code / Copilot) the result — the game plan doc
says every session must record it in the "Current State" section before moving on.

## 5. Where things stand (short version)

- The code that reads `.gpx` files directly (no Guitar Pro needed) is written and unit-tested, but
  has never been run against a real Sheet Happens file — you're about to be the first real test.
- The code that reads tempo data out of `.gp3`/`.gp4`/`.gp5` files is written and tested against a
  real generated Guitar Pro file, so it's on firmer ground.
- Nothing writes an actual `.chart` file yet. That's milestone M3 onward.

See [`SHRED2CHART_GAMEPLAN.md`](SHRED2CHART_GAMEPLAN.md) §7 (Milestones) and §8 (Current State) for
the detailed, up-to-date picture.

## 6. Project layout

```
shred2chart/          the actual tool (Python package)
  gpx_reader.py        reads .gpx files directly
  tempo.py             reads tempo data out of .gp3/.gp4/.gp5 files
  cli.py               the `shred2chart` command
tests/                 automated tests (run with `pytest`)
test_data/             put your real .gpx/.gp5 files here (git-ignored)
SHRED2CHART_GAMEPLAN.md the actual project plan — read this for "why"
```

## 7. If something goes wrong

- `pytest` failing: paste the error to your coding agent — don't try to work around it by editing
  test files.
- `shred2chart: command not found`: re-run `pip install -e ".[dev]"` — the install step registers
  that command.
- A `verify-m0` run that errors out (rather than just showing mismatched numbers) is itself useful
  information — it likely means the `.gpx` reader's assumptions don't hold for your file. Share the
  exact error with your coding agent.
