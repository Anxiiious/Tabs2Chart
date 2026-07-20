# Handoff: Local Development Setup

## What You Have

A working tool that converts Guitar Pro tabs (`.gp` files) into playable Clone Hero charts. You provide a song file, it outputs a folder with `notes.chart` and `song.ini` ready to drop into Clone Hero.

## One-Time Setup (Do This First)

1. Open terminal in this folder
2. Run:
   ```bash
   pip install -e ".[dev]"
   ```
   This installs the tool and its one dependency (PyGuitarPro).

3. Verify it works:
   ```bash
   pytest
   ```
   You should see all tests pass. If anything fails, something's broken.

## What You Can Do Right Now

All commands start with `shred2chart`. Here's what each does:

| Command | What it does |
|---------|-------------|
| `shred2chart dump-tempo your_song.gp` | Show all tempo and time-signature changes as JSON |
| `shred2chart list-tracks your_song.gp` | List all tracks in the file with their names (pick which one to convert) |
| `shred2chart dump-ir your_song.gp --track 0` | Show every note on a track (pitch, fret, string, techniques like hammer-ons) as JSON |
| `shred2chart convert your_song.gp` | **THE MAIN ONE** — converts GP file to playable chart folder |

## The Important Command: `convert`

This is what makes the charts. Run it like:

```bash
shred2chart convert your_song.gp --audio your_song.flac
```

(`--audio` is optional — omit it and just drop `song.ogg` in yourself later, see below.)

It creates a folder `songs/Artist - Title/` with two files:
- `notes.chart` — the actual chart (lanes, notes, tempo, sections)
- `song.ini` — metadata (song name, artist, charter)

**Optional flags:**
- `--out /path/to/folder` — put the output somewhere else (default is `songs/` folder)
- `--audio your_song.flac` — auto-converts and drops in `song.ogg` (needs ffmpeg on PATH, or the bundled `ffmpeg/` folder in this repo)
- `--lead-in-bars 2` — bars of silence before the first note (default **2**), so the highway scrolls before play starts and Clone Hero's audio calibration has something to judge against. Set to `0` to disable.
- `--offset-ms 250` — *extra* fine-tune offset on top of the lead-in, for after you calibrate in Moonscraper/Clone Hero (default 0)
- `--tracks 1,0` — pick specific tracks and their order (default: auto-picks guitar tracks)

## Important: the chart now tracks repeats and D.S. al Coda navigation

Real tabs don't play top-to-bottom in the order they're written — repeat barlines replay
sections, 1st/2nd endings are conditional, and some songs jump around with D.S. al Coda
(Segno/Coda) markers. **If you playtest a song and the guitar is out of sync — especially
if the drift gets *worse* the longer the song plays** — that's this exact issue. It should
already be fixed for `.gp` (GP7 zip) files: `shred2chart` now simulates the real play order
before charting anything, instead of walking the tab in written order. Sanity check to
avoid re-diagnosing something already fixed: `git log --oneline` should show commits
mentioning "repeat" and "D.S. al Coda" — if a new song you convert still drifts, tell your
coding agent, this might be a case (nested Codas, D.C./Fine markers) the fix doesn't cover
yet (see `SHRED2CHART_GAMEPLAN.md` §10, Open Questions).

## After You Convert a Song

**If you passed `--audio your_song.flac` to `convert`**, `song.ogg` is already in the
folder — skip to step 4.

**Otherwise:**

1. Take the generated folder (e.g., `songs/Senses Fail - Still Searching/`)
2. Convert your audio file from FLAC to OGG:
   ```bash
   ffmpeg -i song.flac -q:a 6 song.ogg
   ```
3. Drop `song.ogg` into that folder
4. Copy the whole folder into Clone Hero's `Songs` directory
5. Launch Clone Hero and play it

## How It Works (High Level)

1. **Extract**: Reads `.gp` file directly (no Guitar Pro needed), pulls out every note with all the techniques (hammer-ons, slides, palm mutes, bends, taps, etc.)
2. **Blend**: If the file has multiple guitar tracks, picks the most interesting part for each section of the song (blends lead into rhythm smoothly)
3. **Map**: Converts notes to Clone Hero's 5-lane system (green/red/yellow/blue/orange), handles special cases like open chugs and tied notes
4. **Emit**: Writes the `.chart` file with proper tempo, time signatures, and note events

## Key Files

- `shred2chart/cli.py` — the `convert` command and others
- `shred2chart/blend.py` — blends lead + rhythm tracks per section
- `shred2chart/mapper.py` — maps notes to lanes
- `shred2chart/chart_writer.py` — writes `.chart` and `.ini` files
- `shred2chart/ir_gpif.py` — reads note data from GP files
- `shred2chart/gpif_tempo.py` — reads tempo/time-sig from GP files
- `tests/` — all the unit tests (run with `pytest`)

## What If Something Breaks?

- `pytest` failing? Something's genuinely wrong — check the error message
- `shred2chart: command not found`? Re-run `pip install -e ".[dev]"`
- A chart doesn't sound right? Run `dump-ir` on the GP file and eyeball a few notes against the actual Guitar Pro editor to spot-check

## Next Steps

1. Pick a song from your Sheet Happens album
2. Run `shred2chart list-tracks song.gp` to see what's in it
3. Run `shred2chart convert song.gp --audio song.flac` to make a chart with audio included
4. Test in Clone Hero or Moonscraper
5. If it still doesn't sync right after the built-in lead-in, use `--offset-ms` to fine-tune
   (see "Important: the chart now tracks repeats..." above if the drift gets *worse* over
   the course of the song — that's a specific, mostly-fixed bug, not a calibration issue)

**Outstanding as of 2026-07-19:** the repeat/D.S. al Coda timing fix and the 2-bar lead-in
are both implemented and verified mathematically, but haven't had their in-game playtest
result reported back yet. If you're picking this up, that's the natural next thing to
check and log in `SHRED2CHART_GAMEPLAN.md` §8 (Current State).

## Questions?

- For how-tos: see `README.md`
- For the full design/architecture: see `SHRED2CHART_GAMEPLAN.md`
- For bugs or weird edge cases: the test files show what's working and what's not

Good luck! 🎸
