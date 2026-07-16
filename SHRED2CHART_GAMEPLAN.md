# SHRED2CHART — Sheet Happens → Clone Hero Converter

**Game plan / handoff doc.** This file is the source of truth for project state. It is read by
multiple coding agents (Claude Code and VS Code Copilot, swapped between token-limit breaks).
**Every agent session MUST update the "Current State" and "Decision Log" sections before ending.**
Do not assume conversational context — everything needed to resume lives in this file.

---

## 1. Project Goal

Convert Sheet Happens official Guitar Pro tabs (.gpx) into playable Clone Hero charts (.chart + song.ini), preserving the tab's tempo map so output is rhythm-synced to the real recording out of the box.

**Why this works:** Sheet Happens tabs are transcribed against the actual recordings, including tempo drift. Their tempo automations become the .chart SyncTrack nearly verbatim. Only a single global audio offset needs calibration per song.

### Non-goals (v1)
- General-purpose converter for arbitrary Ultimate Guitar exports
- Difficulty reduction (Hard/Medium/Easy) — Expert only
- Multi-instrument (bass/rhythm/drums) — lead guitar track only
- Automatic audio offset detection (manual via Moonscraper for now)
- Lyrics / vocal charts

### Target user
Single power user. Drop C metalcore / Drop A deathcore repertoire. Heavy open-string chug content — the open-note mapping matters a lot.

---

## 2. Architecture Overview

```
.gpx file
   │
   ▼
[Stage 1: Ingest]        .gpx → parseable form (see §3, two routes)
   │
   ▼
[Stage 2: IR]            Normalized tick-based event model (notes + tempo + TS + techniques)
   │
   ▼
[Stage 3: SyncTrack]     Tempo automations → B/TS events, ramps discretized
   │
   ▼
[Stage 4: Note Mapping]  6 strings × 24 frets → 5 lanes + open (the interesting part, §5)
   │
   ▼
[Stage 5: Emit]          .chart (text) + song.ini → CH song folder
```

Language: **Python**. Plain stdlib + PyGuitarPro where possible. No framework. CLI tool:
`shred2chart input.gpx --out ./songs/ArtistName - SongTitle/`

---

## 3. Stage 1 — Ingest (.gpx problem)

.gpx = Guitar Pro 6 container. **Not a zip** — proprietary BCFS (binary file system) wrapping an XML score (`score.gpif`). PyGuitarPro does NOT read it (caps at .gp5).

### Route A (prototype path — do this first)
Batch-convert .gpx → .gp5 externally (Guitar Pro or TuxGuitar), parse with PyGuitarPro.

**MANDATORY verification before committing to Route A:** convert one Sheet Happens song, dump all tempo events pre-conversion (from the .gpx XML) and post-conversion (from PyGuitarPro's parsed .gp5), diff them. If tempo automations are lost or quantized to bar boundaries, Route A is dead — go to Route B.

### Route B (direct parse — fallback / v1.1)
Reverse-engineered BCFS readers exist (alphaTab in JS/C#; Python implementations exist — research before writing one from scratch). Decompress container → extract `score.gpif` → parse XML. The GPIF XML is cleaner than the .gp5 binary: tempo automations are explicit `<Automation>` elements with bar position + value. No lossy conversion, no Guitar Pro dependency.

**Decision:** start Route A to validate the full pipeline end-to-end. Promote Route B only if A drops tempo data or the external conversion step becomes a workflow drag.

**Update (2026-07-16):** The `.gpx` → `score.gpif` extraction step of Route B got built anyway (`shred2chart/gpx_reader.py`), because it's needed regardless of which route wins — it's the only way to get "pre-conversion" tempo data to diff against for the M0 check itself. See Current State below for validation status.

---

## 4. Stage 2 & 3 — IR and SyncTrack

### Intermediate representation
Everything normalizes to a tick-based event list BEFORE mapping decisions. .chart standard resolution: **192 ticks per quarter note** — use it internally so Stage 5 is a straight dump.

Per-note IR fields:
- `tick` (position), `duration_ticks`
- `pitch` (MIDI number), `string` (1–6/7), `fret`
- technique flags: `hammer_on`, `pull_off`, `tap`, `slide_in/out`, `palm_mute`, `dead_note`, `bend`, `vibrato`, `tremolo_picked`
- `chord_id` (notes struck simultaneously share one)

### SyncTrack generation (first-class feature — the whole point of the project)
- Extract every tempo automation and time-signature change → `B <bpm*1000>` and `TS <num>` events at tick positions.
- **Linear tempo ramps:** GP supports gradual tempo automation; .chart has no ramp concept. Discretize: one stepped B event per beat across the ramp span. Per-beat resolution is sufficient.
- **Mid-bar tempo changes:** legal in GP, legal in .chart. Do not assume tempo events land on barlines.
- **Anacrusis / pickup measures & count-in bars:** classic off-by-one-measure bug. Handle explicitly in tick math — bar 1 of the GP file is not necessarily tick 0 of musical content.
- **Global audio offset:** single `Offset` value in .chart / `delay` in song.ini. v1: default 0, user calibrates in Moonscraper. Leave a `--offset-ms` CLI flag.

---

## 5. Stage 4 — Note Mapping (the actual hard part)

Core principle: this is **lossy compression, not translation**. Optimize for "feels like the riff," not literal encoding.

### Rules, in priority order
1. **Open-string chugs → CH open note** (`N 7`). Any note on the lowest string at fret 0 (or the primary chug pitch in drop tunings) maps to open. Palm-muted low-string runs are the bread and butter of the target repertoire.
2. **Pitch contour, not absolute pitch.** Sliding window (start: ~2 bars) tracks relative melodic motion. Riff ascends → lanes ascend. Reset/re-center window at phrase boundaries (rests ≥ 1 beat, or section markers from the GP file). Absolute mapping is forbidden — a 1-octave riff and a 3-octave solo both have to fit 5 lanes.
3. **Chords by interval spread:** power chords (root+5th) → two adjacent lanes. Wider voicings (root+octave+, 3+ note chords) → wider lane spread, max 3 lanes for playability. Chord root follows the contour rule; spread is relative to root.
4. **Repeated notes stay on the same lane.** Do not jitter identical consecutive pitches across lanes.
5. **Techniques → CH mechanics:**
   - hammer-on / pull-off flags → forced HOPO (`N 5` flag)
   - tap flag → tap note (`N 6` flag)
   - fast slides → trill-style lane walk; slow slides → sustain on origin note
   - tremolo picking → keep as individual notes (CH players expect the strum wall)
   - dead notes / ghost notes → same lane as neighboring context, no special marking (v1)
6. **Sustain threshold:** notes shorter than ~1/8 at local tempo get zero sustain (CH convention). Longer → sustain = duration_ticks, trimmed to leave a gap before the next note on that lane.

### Tuning knobs (expose as config, tune against real output)
- contour window size, phrase-boundary rest threshold, max chord width, sustain cutoff, HOPO auto-threshold distance.

### Quality bar
Output should be playable and *recognizable* without manual edits; Moonscraper is a polish pass, not a rescue pass.

---

## 6. Stage 5 — Emit

- **.chart format, not notes.mid.** Plain text, trivially writable, Moonscraper-native. Sections: `[Song]`, `[SyncTrack]`, `[Events]` (section names from GP markers — free flavor, do it), `[ExpertSingle]`.
- **song.ini** alongside: name, artist, charter, delay, diff_guitar, song_length.
- Output a complete CH song folder. Audio file placement is the user's job (drop song.ogg in the folder); tool prints a reminder.
- Reference spec: the community .chart format doc (Moonscraper repo / FireFox's .chart spec). Agent: fetch and pin the exact `N` flag semantics before writing the emitter — do not code note flags from memory.

---

## 7. Milestones

- [ ] **M0 — Route A validation:** convert 1 song .gpx→.gp5, diff tempo events. GO/NO-GO on Route A. *(Blocks everything.)* Tooling to do this diff now exists (`shred2chart verify-m0`) but has not been run against a real Sheet Happens file yet — see Current State.
- [ ] **M1 — Parse & IR:** PyGuitarPro → IR event list. Verify: dump IR as JSON, spot-check note count / positions against the tab in Guitar Pro.
- [ ] **M2 — SyncTrack:** IR tempo events → `[SyncTrack]`. Verify: load bare chart (no notes) in Moonscraper with real audio, confirm barlines track the recording through tempo changes.
- [ ] **M3 — Emitter skeleton:** naive 1:1 mapping (pitch mod 5, no logic) → valid .chart that loads in Moonscraper + CH. Proves the plumbing.
- [ ] **M4 — Real mapping:** contour + opens + chords + HOPO/tap rules. Verify: play it. Iterate knobs.
- [ ] **M5 — CLI polish:** batch mode, song.ini metadata from GP file header, --offset-ms flag.
- [ ] **v1.1+ (parking lot):** Route B direct .gpx parsing · difficulty reduction · onset-detection auto-offset · rhythm/bass tracks.

Each milestone's verification step is mandatory before checking it off. M2 especially — if the SyncTrack is wrong, everything downstream is wasted work.

---

## 8. Current State

> **AGENTS: update this section every session. Format: date, what was done, what's next, any blockers.**

- 2026-07-16 — Planning complete, this doc created. Nothing coded. Next: M0.
- 2026-07-16 — Repo scaffolded (Claude Code session). Built:
  - `shred2chart/gpx_reader.py` — decompresses BCFZ and unpacks the BCFS virtual filesystem to extract `score.gpif` straight out of a `.gpx`, no Guitar Pro/TuxGuitar needed for this step. Implemented from the reverse-engineering writeup at github.com/Antti/rust-gpx-reader (best public source found), with one correctness fix over that reference (its back-reference copy truncated overlapping runs; ours copies byte-by-byte so repeats work). Covered by unit tests using hand-built byte fixtures — **not yet validated against a real .gpx file**, since none exists in this repo or environment.
  - `shred2chart/tempo.py` — dumps tempo + time-signature events (tick, bpm / numerator+denominator) from a parsed `.gp3/.gp4/.gp5` via PyGuitarPro. Validated with a real PyGuitarPro round-trip test (write a song with tempo + TS changes, parse it back, confirm the dump matches).
  - `shred2chart/cli.py` — `shred2chart dump-gpif`, `dump-tempo`, `verify-m0` commands.
  - `tests/` — 8 passing tests (`pytest`), covering both modules above.
  - **Blocker:** M0 can't actually be checked off without a real Sheet Happens `.gpx` file and a Guitar Pro/TuxGuitar conversion of it to `.gp5` — this environment has no GUI apps and no sample tab. Next agent/session: get a real `.gpx` from the user, run `shred2chart verify-m0 song.gpx song.gp5`, and make the GO/NO-GO call on Route A per §3.

---

## 9. Decision Log

> **AGENTS: append decisions with rationale. Never silently reverse a logged decision — log the reversal.**

- 2026-07-16 — Route A (external .gpx→.gp5 conversion) chosen as prototype path; Route B (direct BCFS parse) deferred pending M0 results.
- 2026-07-16 — .chart output chosen over notes.mid (text format, Moonscraper-native, trivial emitter).
- 2026-07-16 — Expert/lead-only scope for v1; difficulty reduction explicitly deferred.
- 2026-07-16 — Internal resolution fixed at 192 ticks/quarter to match .chart convention.
- 2026-07-16 — Built the `.gpx` → `score.gpif` extractor (normally Route B territory) ahead of schedule, because M0's own verification step needs a "pre-conversion" tempo source and there's no other tool available to produce one. This does not change the Route A vs B decision — it's shared infrastructure both routes need. Implementation follows github.com/Antti/rust-gpx-reader's reverse-engineered BCFS/BCFZ spec (no official spec exists); flagged as unvalidated against real files until M0 runs.

---

## 10. Open Questions

- Does TuxGuitar's .gpx→.gp5 conversion preserve mid-bar and linear tempo automations? (M0 answers this.)
- Sheet Happens 7-string tabs (Drop A material): does the open-note rule apply to string 7 fret 0 only, or lowest-two-strings chug clusters? Decide during M4 with real material.
- GP "let ring" and harmonics flags — ignore or map? (Default: ignore, revisit if charts feel wrong.)
- Does the `score.gpif` XML tempo `<Automation>` schema match what's assumed in `shred2chart/cli.py`'s `verify-m0` instructions? Not yet parsed programmatically (kept as a manual eyeball step) because no real sample file was available to confirm the exact tag names. Worth automating once M0 produces a real `score.gpif` to look at.
