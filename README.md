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
| `shred2chart dump-gpif song.gp` | Pulls the raw `score.gpif` XML out of a `.gp` or `.gpx` file and saves it next to it. |
| `shred2chart dump-tempo song.gp` | Prints every tempo/time-signature change found in the file, as JSON. Works directly on `.gp`/`.gpx`, or on a `.gp3`/`.gp4`/`.gp5` via PyGuitarPro. |
| `shred2chart verify-m0 song.gpx song.gp5` | For the older `.gpx` format only (see below): compares tempo read directly against tempo from a converted `.gp5`, and reports GO/NO-GO automatically. This is milestone **M0** from the game plan. |

None of these produce a playable Clone Hero chart yet — that's a later milestone (M3/M4 in the
game plan).

## 4. Your next concrete step

**If your tab is a `.gp` file** (modern Guitar Pro 7/8 — this turned out to be what real Sheet
Happens tabs actually are): you're already most of the way there. Just run:

```bash
shred2chart dump-tempo your_song.gp
```

No conversion, no TuxGuitar, no extra app needed — it reads the tempo map straight out of the
file. Check the printed BPM values and bar positions against what you'd expect for that song. If
that looks right, tell your coding agent — that confirms milestone **M0** for this file, and the
next step becomes M1 (pulling out the actual notes, not just tempo).

**If your tab is an older `.gpx` file** (Guitar Pro 6): this format needs an extra verification
step, since reading it directly relies on a reverse-engineered (unofficial) spec that hasn't been
tested on a real file yet.

1. Put the `.gpx` somewhere handy, e.g. this repo's `test_data/` folder (git-ignored, so it's fine
   to drop real files there — they won't get committed).
2. Convert that same file to `.gp5` using [TuxGuitar](https://sourceforge.net/projects/tuxguitar/)
   (free) or Guitar Pro if you own it: open the `.gpx`, then "Save As" / "Export" → Guitar Pro 5
   format. Save it next to the original, e.g. `test_data/song.gp5`.
3. Run:
   ```bash
   shred2chart verify-m0 test_data/song.gpx test_data/song.gp5
   ```
4. It reads the `.gpx` directly, parses the converted `.gp5` via PyGuitarPro, and automatically
   compares the two. **GO** means they matched — the direct-read path is trustworthy for this file.
   **NO-GO** means something's off; the printed diff shows exactly which events didn't match.

Whatever you find, tell your coding agent (Claude Code / Copilot) the result — the game plan doc
says every session must record it in the "Current State" section before moving on.

## 5. Where things stand (short version)

- Real Sheet Happens tabs turned out to be modern Guitar Pro 7 `.gp` files (plain zip archives),
  not the older `.gpx` format the game plan originally assumed. Reading tempo/time-signature data
  directly out of `.gp` files is written, tested, and confirmed against a real file — no external
  app needed for this part.
- The `.gpx` (GP6, BCFS/BCFZ) reader is written and unit-tested against hand-built fixtures, but
  has never been run against a real `.gpx` file — only needed if some of your tabs turn out to be
  in that older format.
- The code that reads tempo data out of `.gp3`/`.gp4`/`.gp5` files (PyGuitarPro) is tested against
  a real generated Guitar Pro file.
- Nothing writes an actual `.chart` file yet, and note/technique data (as opposed to just tempo)
  hasn't been tackled — that's milestone M1 onward.

See [`SHRED2CHART_GAMEPLAN.md`](SHRED2CHART_GAMEPLAN.md) §7 (Milestones) and §8 (Current State) for
the detailed, up-to-date picture.

## 6. Project layout

```
shred2chart/          the actual tool (Python package)
  gpx_reader.py        reads .gp/.gpx container files directly, extracts score.gpif
  gpif_tempo.py         reads tempo/time-signature data out of a score.gpif XML
  tempo.py              reads tempo data out of .gp3/.gp4/.gp5 files (via PyGuitarPro)
  cli.py                the `shred2chart` command
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
