# Tabs2Chart (shred2chart)

Converts Sheet Happens Guitar Pro tabs (`.gpx`) into Clone Hero charts (`.chart` + `song.ini`),
preserving the tab's tempo so the chart is rhythm-synced to the real recording.

**Full plan, current progress, and open decisions live in [`SHRED2CHART_GAMEPLAN.md`](SHRED2CHART_GAMEPLAN.md).**
That file is the project's source of truth — read it before diving deeper than this README.

**Status:** produces playable (if rough) charts — `shred2chart convert` writes a complete Clone
Hero song folder. See "Where things stand" below.

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

Commands so far, run as `shred2chart <command>`:

| Command | What it does |
|---|---|
| `shred2chart dump-gpif song.gp` | Pulls the raw `score.gpif` XML out of a `.gp` or `.gpx` file and saves it next to it. |
| `shred2chart dump-tempo song.gp` | Prints every tempo/time-signature change found in the file, as JSON. Works directly on `.gp`/`.gpx`, or on a `.gp3`/`.gp4`/`.gp5` via PyGuitarPro. |
| `shred2chart list-tracks song.gp` | Lists each track's index and name — check this before `dump-ir`, since track 0 isn't reliably "the guitar" (see below). |
| `shred2chart dump-ir song.gp --track N` | Prints every note on the given track — tick, pitch, string, fret, chord grouping, and technique flags (hammer-on/pull-off, slide in/out, palm mute, dead note, bend, tap, vibrato, tremolo picking, let ring, ties, accent, ghost note) — as JSON. |
| `shred2chart convert song.gp` | **The main event**: converts a `.gp` file into a Clone Hero song folder (`notes.chart` + `song.ini`), blending guitar tracks per section so leads and rhythm both get played. `--tracks 1,0` to control which tracks and their priority; `--audio song.flac` to auto-convert and include `song.ogg`; `--lead-in-bars N` (default 2) for calibration-friendly silence before the first note; `--offset-ms` for fine-tuning audio sync on top of that. |
| `shred2chart verify-m0 song.gpx song.gp5` | For the older `.gpx` format only (see below): compares tempo read directly against tempo from a converted `.gp5`, and reports GO/NO-GO automatically. This is milestone **M0** from the game plan. |

`convert` is the one that makes something playable; the rest are inspection tools that show you
(and your coding agent) what's inside a file.

## 4. Your next concrete step

**If your tab is a `.gp` file** (modern Guitar Pro 7/8 — this turned out to be what real Sheet
Happens tabs actually are): you're already most of the way there. Run:

```bash
shred2chart dump-tempo your_song.gp
shred2chart list-tracks your_song.gp
shred2chart dump-ir your_song.gp --track N    # pick N from list-tracks — see note below
```

No conversion, no TuxGuitar, no extra app needed — it reads everything straight out of the file.
**Pick the track carefully**: in real files seen so far, track 0 is sometimes "Rhythm Guitar" while
the interesting part is "Lead Guitar" at track 1 — `list-tracks` shows you the names so you're not
guessing.

The most valuable check you can do right now (this is milestone **M1**'s own verification step,
and no coding agent can do it — it needs a human with the Guitar Pro app or TuxGuitar open):
open the same song in Guitar Pro/TuxGuitar side-by-side with the `dump-ir` output, and spot-check
that the note count and a few positions match what you see on the actual tab. Tell your coding
agent what you find either way — a mismatch is exactly the kind of thing worth catching now,
before more is built on top of it.

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
- Per-note data (pitch, string, fret, chord grouping, techniques like hammer-on/pull-off, slides,
  palm mute, bends, taps, vibrato, tremolo picking) is now extracted for both `.gp`/`.gpx` and
  `.gp3`/`.gp4`/`.gp5` files (`dump-ir`), and every technique flag has shown up with a plausible
  count against a real file. This is milestone M1, and it's not fully checked off yet — it still
  needs a human to spot-check the output against the tab open in actual Guitar Pro (see §4 above).
- `shred2chart convert` now writes a complete Clone Hero song folder. The note mapping is still
  the simple placeholder version (lane choices will look jumpy — the smart "contour" mapping is the
  next milestone), but charts load, sections blend lead/rhythm tracks, chugs come out as open
  notes, and hammer-ons/taps carry over. **The single most useful thing you can do now: convert a
  song with `--audio song.flac` and try it in Clone Hero or Moonscraper.**
- **Timing fix (2026-07-19):** an actual playtest surfaced a real bug — charts drifted
  progressively later than the audio, because the converter walked the tab in *written* order
  instead of the *performance* order Guitar Pro actually plays (repeat barlines, 1st/2nd
  endings, and D.S. al Coda/Segno-Coda navigation all replay or skip material). This is now
  simulated (`gpif_tempo.compute_playback_order`) for `.gp` (GP7) files and verified against a
  real song's embedded section timestamps to within about a second, start to finish. Also added
  a `--lead-in-bars` flag (default 2) so charts don't start on tick 0 — Clone Hero's audio
  calibration needs a beat of silence to judge against. **Not yet done:** the in-game playtest
  of this fixed version hasn't been reported back; the identical bug in the PyGuitarPro
  (`.gp3`/`.gp4`/`.gp5`) path was left unfixed since no real file needing it has turned up there.
  See `SHRED2CHART_GAMEPLAN.md` §8's 2026-07-19 entry for the full story.

See [`SHRED2CHART_GAMEPLAN.md`](SHRED2CHART_GAMEPLAN.md) §7 (Milestones) and §8 (Current State) for
the detailed, up-to-date picture.

## 6. Project layout

```
shred2chart/          the actual tool (Python package)
  gpx_reader.py        reads .gp/.gpx container files directly, extracts score.gpif
  gpif_tempo.py         reads tempo/time-signature data out of a score.gpif XML
  ir_gpif.py            reads per-note data out of a score.gpif XML
  tempo.py              reads tempo data out of .gp3/.gp4/.gp5 files (via PyGuitarPro)
  ir_gp.py               reads per-note data out of .gp3/.gp4/.gp5 files (via PyGuitarPro)
  blend.py              blends lead + rhythm tracks per section into one playable line
  mapper.py             maps notes onto the 5 Clone Hero lanes (naive version)
  chart_writer.py       writes notes.chart + song.ini
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
