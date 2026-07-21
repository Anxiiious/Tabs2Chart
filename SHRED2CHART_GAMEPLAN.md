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

- [x] **M0 — Route A validation:** superseded by direct GPIF parsing, which turned out to be necessary anyway since real files are GP7 zip (§3). Validated tempo fidelity against 3 real files (constant tempo, real tempo changes, real time-signature changes) — GO on direct parsing. See Current State.
- [~] **M1 — Parse & IR:** IR event list, both via PyGuitarPro (`.gp3/.gp4/.gp5`) and direct GPIF parsing (`.gp`/`.gpx`) — see Current State. Dumped as JSON and spot-checked programmatically (note counts, chord grouping, every technique flag exercised) against 3 real files. **Not yet done:** the milestone's own verification step — visually spot-checking note count/positions against the tab open in Guitar Pro — needs a human with the GUI; no coding agent session has that. Consider this milestone open until someone does that check.
- [~] **M2 — SyncTrack:** built (`chart_writer.py`); emits B/TS events at 192 res from the IR tempo events, spec-pinned syntax. Awaiting the human Moonscraper/audio verification step.
- [~] **M3 — Emitter skeleton:** built (`mapper.py` + `convert` CLI); naive pitch-mod-5 mapping plus the three cheap high-value rules (tie merge, open-note chugs, HOPO/tap flags) and section-level track blending. Full charts generate for all real files. Awaiting the human playtest.
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
- 2026-07-17 — **Major finding, changes the plan:** got a real user file (`Brag.gp` by The Home Team). It is **NOT** a GP6 `.gpx`/BCFS container — it's a **GP7 `.gp` file, which is a plain zip archive** (`unzip`-able, confirmed via `zipfile.is_zipfile`). `Content/score.gpif` inside decodes as plain UTF-8 XML with no encryption (GP version `7.6.0`; only GP8 is known to encrypt this file). If the rest of the user's Sheet Happens library is also GP7, **the BCFS/BCFZ reverse-engineering in `gpx_reader.py` may never be needed** — real files skip straight to zip extraction, which is one line of stdlib `zipfile`, not a reverse-engineered binary format.
  - Confirmed the real `score.gpif` schema for tempo/time signature (previously an open question, §10): `<MasterTrack><Automations><Automation><Type>Tempo</Type><Bar>N</Bar><Position>P</Position><Value>"bpm ref"</Value></Automation></Automations></MasterTrack>`, and per-measure `<MasterBars><MasterBar><Time>N/D</Time>...</MasterBar></MasterBars>` (one MasterBar per musical bar; `<Time>` is omitted when unchanged from the previous bar — this repo's parser now inherits correctly, and a test in `test_gpif_tempo.py` catches regressions on that specific point since an early implementation got it wrong). `Brag.gp` itself has a constant 123 bpm, straight 4/4 throughout, 98 bars — no tempo drift to test the interesting case, but section markers (`<Section><Text>`) are present ("Intro" etc.) which the game plan's Stage 5 wants for `.chart` `[Events]`.
  - Built `shred2chart/gpif_tempo.py`: parses tempo + time-signature events directly out of `score.gpif` XML, no Guitar Pro/TuxGuitar conversion involved at all. Output shape matches `shred2chart/tempo.py` exactly so the two are diffable. Unit tested (including the inherited-`<Time>` bug above) and confirmed against the real `Brag.gp`/`Brag.gpif`.
  - Extended `shred2chart/gpx_reader.py` to read zip-based `.gp` containers transparently (previously it just rejected zips with a "go unzip it yourself" error). `extract_gpif()` now works on both formats. Also added a check for the GP8-encrypted-content case (non-UTF8 or non-XML `score.gpif`) so it fails with a clear message instead of silently returning garbage.
  - `shred2chart dump-tempo` now dispatches by extension: `.gp`/`.gpx` → direct GPIF parse (`gpif_tempo`), anything else → PyGuitarPro (`tempo`). Confirmed working end-to-end against `Brag.gp`.
  - `shred2chart verify-m0` upgraded from "print both sides, you eyeball it" to an actual automated diff (matches events by type+value within a small tick tolerance, reports GO/NO-GO). Still useful for validating Route A specifically (does external GP5 conversion preserve tempo fidelity?), but for GP7 files that question is largely moot now — `dump-tempo` on the `.gp` directly already gets the answer with zero external dependencies.
  - **Net effect on M0:** for GP7-format tabs, M0 is effectively resolved as **GO for direct parsing (Route B)** — no conversion step, no GUI app, no PyGuitarPro dependency needed for tempo. Whether this generalizes to the rest of the user's library (and to note/technique data, not just tempo — that's M1, still untouched) is the open question for next session. `Brag.gp`/`Brag.gpif` are the first real reference files but are **git-ignored** (`test_data/`) as the user's personal content, not committed.
- 2026-07-17 — Second real file tested: `Bite The Hook` by Incendiary, this time a native `.gp5` (not converted from anything — `shred2chart/tempo.py`'s PyGuitarPro path, no code changes needed). Ran clean: constant 171 bpm, 99 measures, 4 tracks (`Guitar 1`, `Guitar 2`, `Bass`, `Drums` — confirms multi-track files are normal, Stage 4's "lead guitar track only" scope will need to pick the right track index, not always assume track 0 is the guitar we want... it does look like it here, but worth double-checking per song). More useful than `Brag.gp` for time-signature coverage: this song has real mid-song meter changes (4/4 → 6/4 → 4/4 repeatedly, later brief 3/4 sections), all correctly detected. Still no example of a *tempo* change (both real files so far are constant-tempo) — the open question about tempo ramps/mid-bar tempo automation ticks remains unverified either way.
- 2026-07-17 — Third real file: `A Pale Light Lingers` by Boundaries, another GP7 `.gp` (zip) — this one exported with a slightly different file set inside (`Content/ScoreViews/`, `Content/Stylesheets/`, `meta.json` present; `Brag.gp` didn't have those) but same `VERSION 7.0` / same `score.gpif` schema, extracted and parsed fine with no code changes. **First file with actual tempo changes** — 8 distinct tempos (200→160→200→108→140→120→107→140 bpm) at bars 0/8/20/36/48/64/72/105. Cross-checked the raw `<Automation>` XML directly against `gpif_tempo.py`'s tick output: every single one matches exactly, which is real validation of the Bar-index → tick math (previously only exercised at Bar 0). Still true for every real file seen so far, though: every tempo automation has `Position=0` (bar-start) and `Linear=false` (no ramp) — so the two riskiest open questions (mid-bar `<Position>` fraction semantics, and how to discretize a `Linear=true` ramp) remain completely untested. Worth specifically asking for a song known to have a tempo *ramp* (gradual speed-up/slow-down) or a click/tempo change that lands mid-bar, if one exists in the library.
- 2026-07-17 — **Started M1** (per-note IR). Researched the real `score.gpif` note schema directly from `Brag.gpif` and `Boundaries.gpif` (both already on disk from M0 work): top-level `<Bars>`/`<Voices>`/`<Beats>`/`<Notes>`/`<Rhythms>` collections cross-referenced by id (see `shred2chart/ir_gpif.py`'s module docstring for the full shape). Built two parallel extractors matching §4's per-note IR fields (tick, duration_ticks, pitch, string, fret, chord_id, technique flags):
  - `shred2chart/ir_gp.py` — via PyGuitarPro (`.gp3/.gp4/.gp5`), using `Note.realValue`/`.string`/`.value` and `NoteEffect` fields (hammer/slides/palmMute/vibrato/tremoloPicking/bend/ghostNote, plus `TappedHarmonic` for tap). Round-trip tested — and that test caught two real PyGuitarPro gotchas worth remembering: (1) `gp.write()` silently collapses multiple beats in one voice into one unless `Beat.status`/`Note.type` are explicitly set to `normal` (they default to `empty`/`rest`), and (2) chord note order isn't preserved across write/parse (seems to get re-sorted by string).
  - `shred2chart/ir_gpif.py` — direct GPIF parsing, same output shape. Confirmed against real data: `HopoOrigin`/`HopoDestination` (hammer-on/pull-off), `Slide`+`Flags`, `PalmMuted`, `Muted` (dead/ghost note — distinct property from `PalmMuted`, resolving an earlier ambiguity), `Bended`, `Tapped`, `Vibrato` (a direct Note child, not a Property), `LetRing` (empty element), `Tie`, and beat-level `<Tremolo>` (denormalized onto each note in that beat since the IR is note-centric). Duration math (`NoteValue` + `AugmentationDot` + `PrimaryTuplet`) mirrors PyGuitarPro's own `Duration.time` formula.
  - Refactored `gpif_tempo.py` to expose `compute_bar_grid()` so both the tempo and IR gpif-parsers share one bar-tick-grid implementation instead of duplicating it.
  - Added `shred2chart list-tracks` — **needed because of a real finding**: track 0 is not reliably "the guitar." `Boundaries.gp` has a `Rhythm Guitar` track (0) with zero interesting techniques and a `Lead Guitar` track (1) where every single technique flag (hopo, slide, palm_mute, dead_note, bend, tap, vibrato, tremolo_picked, let_ring, tied) shows up with a plausible nonzero count. `Brag.gp` has 5 tracks, three of them identically named `Overdriven Guitar` — which one is "the" lead isn't derivable from the name alone there. `dump-ir` takes `--track` rather than assuming an index.
  - All 15 tests pass (`pytest`); ran `dump-ir` against all 3 real files with no crashes (500/876/1111 notes respectively, once pointed at the right track).
  - **Not done, left for next session:** the milestone's mandated verification step (open the tab in actual Guitar Pro, eyeball note count/positions against the IR dump) needs a human with the GUI.
- 2026-07-17 — **Cross-checked our GP-parsing logic against editor-on-fire (EOF)**, the open-source chart editor the CH community already uses to import Guitar Pro files (github.com/raynebc/editor-on-fire, specifically `src/gp_import.c`). It only reads `.gp3/.gp4/.gp5` (no GP7 support either, which is reassuring — confirms this is a genuinely hard format, not something we're missing an easy library for). Reading its actual binary-parsing logic (not just running it) turned up one real bug and resolved two open questions:
  - **Bug found and fixed:** PyGuitarPro's `NoteEffect.hammer` (and GPIF's `HopoOrigin`) is set on the *origin* note of a hammer-on/pull-off pair, not the destination note that actually plays without picking. `ir_gp.py` had been exposing that flag as-is on the wrong note (`"hopo": effect.hammer`). Fixed by shifting it forward one note, with direction from EOF's own algorithm (destination fret < origin fret ⇒ pull-off, else hammer-on). `ir_gpif.py` already read the correct note (GPIF has an explicit `HopoDestination` marker), but still needed the same fret-comparison logic to get *direction*. Both modules now expose `hammer_on`/`pull_off` instead of one flat `hopo` — matching §4's field names for the first time. Verified the fix actually changes real output sensibly: `Brag.gp`'s old flat `hopo` count of 72 (double-counting both notes of each pair) became `hammer_on: 36` (correctly counting one flag per pair, on the right note).
  - **Resolved:** GPIF's `<Slide><Flags>` bitmask. EOF's source documents the exact GP5+ bit assignments (1=shift slide, 2=legato slide, 4=slide out downward, 8=slide out upward, 16=slide in from below, 32=slide in from above) and GP7's GPIF appears to reuse them unchanged — our one real example, `Flags=2`, decodes as a legato slide-out, consistent with its context. `ir_gpif.py` now exposes `slide_in`/`slide_out` booleans matching `ir_gp.py`'s shape (raw `slide_flags` kept too).
  - Both fixes are documented inline in `ir_gp.py`/`ir_gpif.py`'s module docstrings with the EOF source citation. All 16 tests pass; re-ran `dump-ir` against all 3 real files — every song now produces a sensible, non-doubled hammer_on/pull_off/slide_in/slide_out split.
  - Still unresolved: the hammer_on/pull_off direction inference (in both modules) tracks "the previous note" per track, not per string — a simplification EOF itself makes too, but it means the very first note after a chord (or the note right after a string change) could get misattributed. Not expected to matter for the target repertoire (single-note lead lines) but worth remembering for Stage 4.
- 2026-07-17 — **More EOF cross-checks, per request, looking for further gaps** (not just re-confirming the hopo/slide fix):
  - **Found and fixed a real completeness gap:** `ir_gp.py` wasn't exposing `ghost_note` or `accent` at all, even though PyGuitarPro's `NoteEffect` has real, correctly-populated `ghostNote`/`accentuatedNote`/`heavyAccentuatedNote` fields (confirmed by reading PyGuitarPro's own `gp3.py`/`gp5.py` source — bits `0x04`/`0x40` of the note flag byte, cross-referenced against EOF reading the identical bits the identical way). Added both fields. `ir_gpif.py` gained `accent` too (a real, confirmed `<Accent>N</Accent>` direct child of `<Note>`, same shape as `<Vibrato>`) — but **not** a real `ghost_note`: neither real `.gpif` file contains anything named "Ghost", so it's exposed as an honest always-`False` stub with a comment, rather than guessing a property name. Verified against real files: `Brag.gp` track 1 (not track 0!) has 15 accented notes — another instance of "the interesting data isn't always on track 0."
  - **Noted for Stage 4, not fixed now (out of M1's scope):** EOF doesn't treat a tied note as a new note event — it extends the *previous* note's sustain instead of creating a separate attack. Our IR currently reports each tied note as its own entry (correct raw data — `tied: True` is exactly the flag Stage 4 needs to do this itself), but whoever builds the note-mapping stage needs to remember to merge ties into the prior note's `duration_ticks` rather than treating them as new picks. Documented in both `ir_gp.py`'s and `ir_gpif.py`'s docstrings so it isn't lost.
  - Also confirmed (no fix needed, just cross-referenced): GP7's tuplet math generalizes cleanly past what EOF's GP3/4/5 binary reader has to special-case. EOF hardcodes ratios for sextuplets/septuplets/13-tuplets because the old binary format only stores a note count, not a ratio; GPIF's `<PrimaryTuplet num den>` gives the ratio directly, so `ir_gpif.py`'s formula doesn't need EOF's lookup table.
- 2026-07-17 — Fourth real file: `Still Searching` by Senses Fail (Sheet Happens album test). GP7.6 zip, same schema, parsed with zero code changes. Constant 123 bpm 4/4 (still no mid-bar Position or Linear ramp in any real file — increasingly looks like Sheet Happens charts against a constant click, which would moot the ramp-discretization worry for this library). Five tracks; Guitar 2 is the technique-rich lead candidate (hammer-ons, pull-offs, 5 taps, vibrato), Guitar 1 the palm-mute-heavy rhythm. **Materially strengthens the slide bitmask conclusion:** file contains slide_flags values {1, 2, 4, 20, 32} — and 20 = 16+4 (in-from-below + out-downward) only makes sense as a bit combination, confirming the EOF-derived GP5 bitmask really is reused by GP7 GPIF. The "only one real example (Flags=2)" caveat in the Open Questions is now effectively closed.
- 2026-07-17 — **M2 + M3 built: `shred2chart convert` produces a complete CH song folder** (notes.chart + song.ini) from a `.gp` file. Pipeline: sections (`gpif_tempo.dump_sections`, from the real `<MasterBar><Section><Text>` markers) → per-section track blending (`blend.py`) → naive note mapping (`mapper.py`) → spec-pinned emitter (`chart_writer.py`). Details:
  - **Spec pinned before coding the emitter**, per this doc's own mandate: TheNathannator's GuitarGame_ChartFormats (Format-Overview + 5-Fret-Guitar pages). N 0-4 = GRYBO, 5 = strum/HOPO flip, 6 = tap (overrides HOPO), 7 = open; `B <bpm*1000>`; `TS <num> [log2 den]` with exponent omitted for /4; `[Song] Offset` in seconds; Resolution = 192 emitted (IR's 960 divides exactly by 5).
  - **Track blending (user-requested feature, pulled forward from M4):** real Sheet Happens songs split guitar across tracks, so `convert` blends them — per GP section, each candidate track gets scored (note count + 2× per technique flag, so a sparse flashy lead outbids a chug wall) and the section is taken whole from the winner; switches only at section boundaries so phrases stay intact. Tested on `Still Searching`: sections alternate Guitar 1 (rhythm) / Guitar 2 (lead) plausibly, and the 5 tap notes from Guitar 2 all survive into the chart. Files with no section markers (e.g. `Boundaries.gp`) fall back to 8-bar windows. `--tracks 1,0` overrides; default = all guitar-named tracks.
  - **Mapping is still M3-naive** (pitch mod 5; chords = adjacent lanes, max 3 wide) but includes the cheap rules that matter for the target genre: tied notes merge into the previous note's sustain (the EOF-confirmed behavior, now actually implemented), fret-0 on the lowest-*tuned* string (inferred from pitch−fret per string, so drop tunings work) → open note N7, hammer_on/pull_off → N5 forced flip, tap → N6, sub-eighth notes get no sustain, sustains trimmed to leave a 1/32 gap. Contour mapping remains M4 and replaces `mapper._assign_lanes`.
  - 25 tests pass (blend scoring/tie-breaks, tick conversion, open-note rule, tie merge, chord width cap, flags, sustain trim, full chart text). All 3 GP7 real files convert end-to-end.
  - **Next / blockers:** the human verification for M2+M3 is now one single step — drop each song's audio as `song.ogg` into the generated folder, load in Clone Hero or Moonscraper, confirm (a) barlines track the recording and (b) the chart is recognizably the song. `--offset-ms` exists for audio calibration. Known naive-mapping artifacts to expect: lane choices jump around (no contour logic yet), and repeated identical pitches do stay on one lane only by virtue of pitch-mod-5, not by rule.
- 2026-07-20 — **Modernization & CLI polish pass** (GitHub Copilot coding agent session). Completed all items from the HANDOFF.md checklist. Changes:
  - **Input validation:** All CLI commands (`dump-gpif`, `dump-tempo`, `list-tracks`, `dump-ir`, `convert`, `verify-m0`) now validate their input file (exists, is a file, has an accepted extension) before parsing, via a shared `_validate_input_file` helper. Clear user-facing error messages; exit 1 on failure.
  - **Legacy format support in `convert`:** `convert` now accepts `.gp3`/`.gp4`/`.gp5` files in addition to `.gp`/`.gpx`, dispatching through `ir_gp` (PyGuitarPro) and `tempo.dump_tempo_events` for the legacy path. Sections are not available for legacy files (no GPIF XML); blending falls back to 8-bar windows.
  - **Track selection consistency:** `list-tracks` output now mentions both `--track` (dump-ir) and `--tracks` (convert) so the user knows which flag to use for each command.
  - **`check` command:** New `shred2chart check <song_dir>` subcommand exposes `validation.validate_song_folder` for re-checking generated or manually-edited folders.
  - **Richer validation:** `validate_song_folder` now also checks for `[ExpertSingle]` presence and non-emptiness.
  - **Safe metadata escaping:** `validation.escape_metadata()` strips control chars and escapes `\` and `"` in title/artist before writing to `.chart` and `song.ini`. Used in `chart_writer.build_chart` and `build_song_ini`.
  - **`--version` flag:** `shred2chart --version` reports the installed package version via `importlib.metadata`.
  - **`--quiet`/`--verbose` flags:** Global flags on the top-level parser. `--quiet` suppresses all progress lines; `--verbose` enables `logging.DEBUG`. Replaces the scattered `if not args.json: print(...)` pattern with a consistent `_info()` helper inside `_cmd_convert`.
  - **`--dry-run` on `convert`:** Prints what would happen (output path, note/section counts) without writing any files.
  - **Shell completion:** `argcomplete` added as an optional dev dependency; `PYTHON_ARGCOMPLETE_OK` sentinel at the top of `cli.py`; `argcomplete.autocomplete(parser)` called in `main()` when the package is installed.
  - **Audio UX:** `_ffmpeg_install_hint()` provides platform-specific install instructions when ffmpeg is missing. `_prepare_audio` validates that the output `song.ogg` is non-empty after encoding. When no `--audio` is passed, `convert` prints a step-by-step OGG reminder including the ffmpeg install hint if ffmpeg is absent.
  - **`song_length` in song.ini:** `chart_writer.compute_song_length_ms` computes wall-clock length from the last note tick + tempo map; written as `song_length = N` in song.ini.
  - **`--charter` flag:** Lets the user set their charter name for song.ini and the `[Song]` block. Falls back to the GP file's `<SubTitle>` field if present, then `"shred2chart"`.
  - **`--archive` flag:** Zips `notes.chart`, `song.ini`, and `song.ogg` (if present) into `Artist - Title.zip` alongside the song folder for drag-and-drop import.
  - **M4 contour-based lane assignment:** `mapper._assign_lanes` (M3 pitch-mod-5) replaced by a `_ContourTracker` class that maintains a sliding window of recent pitches, maps them proportionally onto 5 lanes, and resets at section-marker ticks or rests ≥ 1 bar. `map_notes` takes an optional `section_ticks` list. The open-string chug rule, tie merge, chord interval-spread voicing, HOPO/tap flags, and sustain policy are all preserved.
  - **Integration test fixture:** `tests/fixtures/sample.gp` — a minimal hand-crafted GP7 zip (2 tempo events, 4 bars, 1 section, 3 notes on 1 track). `tests/test_integration_convert.py` — 15 tests covering the full pipeline, `check` command, error messages, `--dry-run`, `--archive`, `--charter`, `--quiet`, and `--version`.
  - **56 tests pass** (`pytest`).
  - **Next:** Human verification of M2/M3/M4 — drop audio into a generated song folder and play in Clone Hero or Moonscraper. The contour-based mapping should produce more recognisable charts than M3's pitch-mod-5, but lane knobs (`_CONTOUR_WINDOW`, `_MIN_WINDOW_SPAN`, `_REST_RESET_TICKS`) may need tuning against real playtest feedback.
- 2026-07-20 — **Addressed 5 previously-flagged open gaps** (GitHub Copilot coding agent session):
  - **`Linear=true` tempo ramp handling:** `gpif_tempo.dump_tempo_events` now detects `<Linear>true</Linear>` on a Tempo automation and discretizes the ramp into one stepped B event per beat (TICKS_PER_QUARTER spacing, linear interpolation toward the next automation's BPM). If the linear automation is the last one (no known endpoint), it falls back to a single instantaneous event. Two new synthetic-fixture tests added: one verifying 8 per-beat events across a 2-bar ramp from 100→140 bpm, one verifying the fallback for an isolated linear automation.
  - **GP6 `.gpx` BCFS/BCFZ path:** decided to keep (see Decision Log). No code changes — the code is correct and tested with synthetic fixtures; the decision was just previously undocumented.
  - **`ghost_note` in `ir_gpif.py`:** upgraded from always-`False` stub to `"GhostNote" in props`, following GPIF's uniform `<Property name="X"><Enable/></Property>` pattern for all boolean note flags (same pattern as `PalmMuted`, `Muted`, `Tapped`, etc.). Module docstring updated to explain the reasoning and flag the unverified status. New synthetic-fixture test confirms a note with `<Property name="GhostNote">` gets `ghost_note=True` and one without gets `False`.
  - **7-string / Drop A open-note rule:** decided (see Decision Log). §10 updated.
  - **59 tests pass** (`pytest`).
- 2026-07-21 — **Mapper rework session: chord voicing removed, distinct-lane guarantee added,
  contour mechanism rebuilt as a directional wraparound cursor. Drafted and researched only — NOT
  yet run against any real file or the existing test suite** (see the execution addendum below —
  a follow-up session in the same day applied the code, ran and rewrote the tests).

  **Code changes to `shred2chart/mapper.py`:**
  1. Chord interval-spread voicing (`_interval_to_gap`, chord-branch lane spreading, chord width
     capping) removed entirely. It was the actual source of mapping bugs; single-note contour
     mapping was solid. Every note — chord member or not — now goes through the same per-note lane
     assignment path.
  2. Distinct-lane guarantee added for notes sharing a tick (real chords, or blend-seam collisions
     from merging tracks). Same-tick notes are grouped; lane collisions are resolved by nudging to
     the nearest still-free lane (0-4), processed lowest-pitch-first so the anchor note claims its
     natural spot. Different simultaneous notes can never end up silently deduped onto one lane
     (an actual bug in the first draft of change #1, since fixed).
  3. `_ContourTracker` rebuilt: the old version computed each note's lane from its position inside a
     rolling min/max pitch window, which caps out on long runs — a rising scale just pins at orange
     (lane 4) and flatlines. Replaced with an unbounded running cursor: each new distinct pitch moves
     the cursor by a signed step (magnitude from semitone interval via `_interval_to_step`, direction
     from interval sign); the visible lane is `cursor % 5`, producing a proper wraparound "staircase"
     climb instead of a flatline. Cursor resets to 0 (green) on phrase boundaries (section-marker
     tick, or a rest ≥ 1 bar) — same reset triggers as the previous version.

  **Known unresolved issue in the shipped code, flagged not fixed:** in `_assign_group_lanes`, the
  logic placing extra chord-note lanes beyond the first (anchor) note in a same-tick group is a
  placeholder (`preferred = list(lanes.values())[0] if lanes else 0`), not a considered decision —
  chord musical correctness is explicitly out of scope this pass. Needs real review before trusting
  output against a genuinely chord-bearing file.

  **Research/validation, with sourcing quality noted per claim:**
  - The visual "staircase" pattern is a named, standard Clone Hero community convention called a
    **"Ladder"** — confirmed verbatim via the official Clone Hero Wiki dictionary page
    (https://wiki.clonehero.net/books/general-info/page/dictionary): *"A pattern that ascends or
    descends by 2-note 'steps', resembling a ladder or stairs when laid out on the highway."*
  - The underlying mechanism (running position + signed step + wraparound at the ceiling/floor) was
    independently arrived at by a Berkeley MIMS capstone project, **"Tensor Hero: Generating
    Playable Guitar Hero Charts from Any Song"** (Waissbluth, Carr, Popescu, Hu), which mined 450
    real GH/CH charts:
    https://www.ischool.berkeley.edu/sites/default/files/sproject_attachments/tensorhero_capstone.pdf
    Their "note contour" model uses an anchor + motion representation, motion range **[-4, 4]**
    (matches our step-magnitude range), with explicit wraparound when the anchor hits the top/bottom
    of its note-plurality category. Confirms the mechanism shape; does NOT confirm our specific
    step-size-per-semitone bucketing (`_interval_to_step`'s thresholds are still our own guess).
  - The core **"preserve melodic motion over strict pitch consistency"** principle behind contour
    mapping is directly confirmed, verbatim, by Rock Band Network's own authoring documentation:
    http://docs.c3universe.com/rbndocs/index.php?title=Guitar_and_Bass_Authoring — *"Above all,
    guitar authoring is about making the part feel right. Try to preserve melodic motion even if it
    means breaking consistency."* Same page confirms standard gem-wrapping patterns for runs
    exceeding 5 lanes (e.g. Green/Red/Yellow → Red/Yellow/Blue → Yellow/Blue/Orange) and confirms
    that even "sloppy"/chaotic solos should be quantized to the grid (usually 16th, sometimes 32nd
    notes) rather than charted loosely.
  - **RBN's "top four lanes" guidance, checked and CORRECTED from an initial mischaracterization:**
    the source is real (same URL above) but it's specifically a **Medium-difficulty-reduction
    guideline** — where to place Medium's rare orange-gem exposure, using a guitar solo as one
    example of a good "unique section" candidate — not a general rule that Expert solos should be
    charted within lanes 1-4. Do not apply this as "avoid green during solos."
  - **RBN's Trill (MIDI 127) / Tremolo (MIDI 126) markers, confirmed real, then confirmed
    NOT APPLICABLE to this project.** They're a Rock Band-engine-specific mechanic (free-form lanes
    that appear when a player alternates faster than a hardcoded 160ms threshold), tied to RB3's own
    MIDI-based chart format. No equivalent exists in the `.chart` text format we emit — checked
    against the YARG wiki's `notes.chart` page and Moonscraper documentation, neither describes any
    free-form-lane concept. Clone Hero community usage of the word "Trill" (per the Wiki dictionary,
    above) is purely a visual naming convention for an ordinary two-lane alternating pattern, encoded
    as regular `N` note lines — nothing special in the file format. No implementation follows from
    this; it's closed as not applicable.
  - **Two claims from research could NOT be verified and are not being treated as sourced fact:**
    a "cloud of sound" recommendation to prefer continuous 16th notes over sustains for chaotic
    solos, and a claim that RBN docs explicitly recommend forcing HOPOs to preserve a flowing feel
    during wide-timing-gap runs. Neither turned up in direct searches of the RBN/C3 docs. Not adding
    either to the mapper or citing as sourced unless a real citation surfaces.

  **Not done this session:** no code was run against any real file (`Brag.gp`, `Boundaries.gp`,
  `Still Searching`, `Bite The Hook`) or the existing `pytest` suite. Existing unit tests asserting
  old chord-voicing or old windowed-contour behavior are expected to fail against this version —
  that's intentional, they assert removed/changed behavior, not a regression; they need to be
  rewritten, not used to justify reverting the mapper.
- 2026-07-21 — **Execution addendum (follow-up session, branch `claude/mapper-contour-wraparound-lceuej`):**
  the drafted mapper above was applied to the repo and validated:
  - `pytest` run: exactly the three predicted chord-voicing tests failed and were rewritten to
    assert the new behavior (`test_chord_all_notes_kept_on_distinct_lanes`,
    `test_power_chord_two_distinct_lanes`, `test_wide_chords_no_note_loss`). One of them was
    actually failing via the known-unresolved `_assign_group_lanes` placeholder (an open-chug
    chord anchors its extra notes off the OPEN_NOTE lane 7 and dedupes); per the handoff that
    logic was NOT fixed — the broken case is captured as a `strict=True` xfail test
    (`test_open_chug_chord_keeps_all_notes`). **59 pass, 1 xfail.**
  - Staircase verified in code: a synthetic 12-note rising run maps 0→4, wraps to 0, keeps
    climbing (the old tracker would flatline at 4). Fixture smoke test via
    `shred2chart convert tests/fixtures/sample.gp` (repeated pitches correctly stay on one lane).
  - Still pending: `convert` against real `.gp` files (`test_data/` absent in that environment —
    needs the user) to confirm staircasing on real solos, and the chord-placeholder decision.
- 2026-07-21 — **Chord-mapper session: the `_assign_group_lanes` placeholder for a chord's
  non-anchor lanes is replaced with a scored-candidate chord-shape heuristic, generalizing the
  single-note staircase mechanism to chords** (branch `claude/chord-mapper-staircase-ven86a`, per a
  collaborator handoff — design mandate: optimize for chart readability and directional musical
  movement, not literal guitar fingering; no hard adjacency requirement between a chord's notes; no
  ceiling/floor lock on ascending/descending chord progressions; enough phrase-to-phrase variety
  that a moving progression doesn't collapse onto one repeated shape).
  - **`shred2chart/mapper.py` changes:** two new small helpers — `_chord_shape_candidates(k)`
    (enumerates all `C(5,k)` legal ways to place `k` fretted notes on the 5 lanes, ≤10 candidates)
    and `_rank_chord_shape(...)` (ranks each candidate against the previous emitted chord's
    shape/pitch content, the phrase's recent direction trend, and recently-used shapes; returns a
    breakdown dict so `_logger.debug(...)` can show exactly why a candidate won). `_ContourTracker`
    gained four fields (`_last_group_lanes`, `_last_group_pitches`, `_recent_group_lanes`,
    `_recent_anchor_pitches`) that reset alongside the existing cursor-reset triggers (section
    marker, ≥1 bar rest) — no second parallel reset mechanism. `_assign_group_lanes`'s single-note
    path (`len(fretted) == 1`) is untouched — same `contour.raw_lane` call, same
    `_nearest_free_lane` placement — so single-note runs are byte-for-byte unchanged; only the
    `len(fretted) >= 2` branch changed. After a chord is placed, the persistent cursor is resynced
    to the chosen anchor lane (`contour._lane_cursor += chosen_anchor_lane - anchor_preferred_lane`)
    so a later single note continues from where the chord actually landed, not the raw (possibly
    overridden) cursor value. `k > 5` (more than 5 simultaneous fretted notes) falls back to a
    nearest-free-lane chain seeded only from lanes this loop itself assigned — a documented,
    pre-existing, out-of-scope limitation (5 physical lanes can't uniquely fit 6+ simultaneous
    notes either way).
  - **Fixed as a structural side effect, not a separate fix:** the open-chug-chord bug
    (`test_open_chug_chord_keeps_all_notes`, previously `xfail(strict=True)`) is gone — the new
    fretted-note path only ever draws lanes from `_chord_shape_candidates` (always 0-4), never from
    `lanes.values()` where `OPEN_NOTE=7` could leak in as a placeholder seed, which was the actual
    root cause. The `xfail` marker was removed; the now-unused `import pytest` in
    `tests/test_chart_pipeline.py` was removed with it (it was that decorator's only use in the
    file).
  - **Tests:** all pre-existing `TestMapper` tests pass unmodified. Two of them
    (`test_different_chords_never_repeat_same_lanes`, `test_repeated_identical_chord_keeps_lanes`)
    were found, during design, to only exercise the single-note path (their fixtures put a `fret=0`
    note on the chug string, which the pre-existing open-note rule pulls out, leaving one fretted
    note per group) — so they don't actually cover real `k >= 2` chord scoring. Two new tests were
    added to cover that gap for real: `test_ascending_chord_progression_avoids_ceiling_lock` (6
    ascending power chords, `fret > 0` so genuinely `k=2`; asserts no two consecutive shapes repeat
    and the run isn't pinned at the ceiling for its back half) and
    `test_real_repeated_chord_keeps_same_shape` (same `k=2` chord struck 3x, asserts identical
    shape each time). **62 tests pass, 0 xfail.**
  - **Design note for future tuning sessions:** the exact scoring weights (`+3`/`+2`/`+0.5`/`-0.5`/
    `-3`/`-2` in `_rank_chord_shape`) are explicitly tunable knobs, not settled values — same status
    as `_interval_to_step`'s semitone-bucketing thresholds. Enable `logging.DEBUG` to see every
    candidate's rank breakdown per chord when tuning. Empirically, longer ascending `k=2` runs don't
    always produce a strictly-monotonic staircase (some non-adjacent repeats can occur a few chords
    apart, since a 2-note chord's ascending pair can only reach `c[0] <= 3`, so the "match the raw
    cursor exactly" bonus goes silent once the cursor sits at 4 until it wraps past it) — this was
    checked by actually running the code, not just hand-derivation, and the weaker
    "never repeats the immediately preceding shape, and isn't pinned at the ceiling forever"
    invariant (what the new tests assert) held up as the honestly-supportable claim; a stronger
    "globally unique shapes" guarantee is not what this heuristic provides and the tests don't claim
    it.
- 2026-07-21 — **PR #8 review follow-up (external review pass, no correctness issues found —
  "solid point to merge," remaining concerns were refinement, not correctness).** Three items were
  raised; all three addressed with documentation/refactoring, no behavior change (62 tests still
  pass, identical output):
  - **Scoring weights buried as literals in `_rank_chord_shape`** — extracted into eight named
    module-level constants (`_WEIGHT_ANCHOR`, `_WEIGHT_HARMONIC_CHANGE`, `_WEIGHT_UNPINNED`,
    `_WEIGHT_READABLE`, `_WEIGHT_RECENT_REPEAT`, `_WEIGHT_UNJUSTIFIED_REPEAT`,
    `_WEIGHT_CONTRARY_JUMP`, `_WEIGHT_STABILITY`), same convention already used for
    `_RECENT_SHAPES`/`_TREND_WINDOW`. A fuller `@dataclass ShapeWeights` config object was suggested
    as a longer-term direction but not built now — it edges toward the "policy object" pattern the
    original handoff explicitly asked to avoid, and named constants already deliver the actual
    ask (tuning becomes editing a number, not hunting through scoring logic).
  - **Long ascending `k=2` runs producing non-adjacent repeats** — reviewer agreed this isn't a bug
    (eliminating it needs look-ahead/backtracking/DP over future chords, real complexity for a
    readability difference unlikely to matter on an actual highway) and asked for one sentence
    framing it as a deliberate trade-off rather than an oversight. Added to `_rank_chord_shape`'s
    docstring: this is a bounded local optimization (each chord scored only against the previous
    shape + a short recent-shape window, never a global search), a deliberate trade-off for
    determinism and O(1)-per-chord cost.
  - **Cursor resync drift** — reviewer's question was whether `contour._lane_cursor += (chosen -
    preferred)` risks compounding error over a long chord-heavy solo. Analysis: it doesn't —
    `chosen_anchor_lane` and `anchor_preferred_lane` are both already-wrapped values in 0-4, so the
    delta applied is bounded to `[-4, 4]` on every single chord, a one-time correction reflecting
    one real choice, not an accumulating error term (mathematically this already *is* the
    reviewer's own suggested "observed cursor becomes the logical cursor" approach, just expressed
    as a bounded `+=` instead of an absolute set). The existing reset triggers (section marker,
    rest >= 1 bar) already provide the periodic hard boundary a from-scratch design would add.
    Documented inline at the resync site rather than restructured, since no redesign was needed.
  - **Backlog, not implemented this session:** a `tests/` regression corpus (single notes, dyads,
    triads, staircase runs, repeated chords, awkward spreads — one fixture file + expected lane
    output each) so future heuristic tweaks can be checked against "did this actually improve
    something" rather than eyeballing. Real value once weight-tuning against playtest feedback
    starts in earnest; premature before any real chord-bearing file has been tried (still an open
    question below). Logged here so it isn't lost, not built speculatively ahead of that need.

---

## 9. Decision Log

> **AGENTS: append decisions with rationale. Never silently reverse a logged decision — log the reversal.**

- 2026-07-16 — Route A (external .gpx→.gp5 conversion) chosen as prototype path; Route B (direct BCFS parse) deferred pending M0 results.
- 2026-07-16 — .chart output chosen over notes.mid (text format, Moonscraper-native, trivial emitter).
- 2026-07-16 — Expert/lead-only scope for v1; difficulty reduction explicitly deferred.
- 2026-07-16 — Internal resolution fixed at 192 ticks/quarter to match .chart convention.
- 2026-07-16 — Built the `.gpx` → `score.gpif` extractor (normally Route B territory) ahead of schedule, because M0's own verification step needs a "pre-conversion" tempo source and there's no other tool available to produce one. This does not change the Route A vs B decision — it's shared infrastructure both routes need. Implementation follows github.com/Antti/rust-gpx-reader's reverse-engineered BCFS/BCFZ spec (no official spec exists); flagged as unvalidated against real files until M0 runs.
- 2026-07-17 — **Reversal candidate, not yet fully decided:** Route A (external .gpx→.gp5 conversion via Guitar Pro/TuxGuitar) was chosen 2026-07-16 as the prototype path. Real user data shows the actual files are GP7 zip-based `.gp`, where direct GPIF parsing (Route B) requires zero reverse engineering and zero external tools — it's strictly simpler than Route A for this format. Not logging this as a full reversal yet because (a) it's only confirmed for tempo data on one song, not notes/techniques (M1 territory), and (b) we don't yet know if 100% of the user's library is GP7 or if some older `.gpx` GP6 files are mixed in. Next session should get a second real file (ideally one with tempo changes, to actually test the Position-field assumption) and, if GP7 holds up, promote direct GPIF parsing to the primary path in §3 rather than a "fallback."
- 2026-07-20 — **GP6 `.gpx` BCFS/BCFZ path: keep (do not delete).** All 4 real files seen so far are GP7 zip format; the BCFS/BCFZ half of `gpx_reader.py` has never been exercised on a real file. Decision: retain it. Rationale: (a) it is already correct and fully covered by synthetic-fixture unit tests; (b) we cannot yet rule out GP6 files in the user's full library; (c) deleting and re-implementing later is more work than the small ongoing maintenance cost of a ~100-line module. Revisit only if a future audit of the whole library confirms 100% GP7 and the dead-code cost becomes worth paying.
- 2026-07-20 — **7-string / Drop A open-note rule: apply to fret 0 on the single lowest-tuned string only.** `mapper._lowest_tuning_string` already infers open pitch as `pitch − fret` for every string and returns the one with the globally lowest open pitch. For a 7-string Drop A guitar, string 7 at fret 0 will naturally win that contest. The "lowest-two-strings chug clusters" variant was considered but rejected: a cluster of two adjacent open-string hits is already handled correctly by the chord voicing path (two simultaneous notes → interval-spread lanes); duplicating the open-note rule for the second string would require knowing in advance what the "second lowest" open pitch is, adds complexity, and has zero confirmed real-file motivation. If real playtest of Drop A material reveals the current rule is wrong, reopen then.

- 2026-07-21 — **Chord interval-spread voicing (introduced 2026-07-20 as part of the M4 contour
  pass) reversed and removed.** Root cause per direct user feedback: absolute-interval chord
  voicing was producing bad output; single-note contour anchoring was not. Removed outright rather
  than debugged — every note (including chord members) now goes through the same per-note lane
  assignment, with same-tick collisions resolved generically (nearest free lane) instead of voiced
  by musical interval. Logged as a reversal per this doc's own mandate: the 2026-07-20 entry
  describing `_interval_to_gap`-based chord voicing is superseded. (Note: the 2026-07-20
  open-note-rule decision's rationale referenced "the chord voicing path"; that path no longer
  exists, but the decision itself — fret 0 on the single lowest-tuned string only — stands
  unchanged.)
- 2026-07-21 — **Contour tracker mechanism changed from windowed min/max proportional mapping
  (built 2026-07-20) to a directional wraparound cursor.** The windowed version caps lane assignment
  at the window's pitch extremes, flatlining long rising/falling runs at the ceiling/floor lane
  instead of producing the "Ladder"/staircase pattern real charts use. New mechanism: unbounded
  running cursor, advanced by a signed step per note-to-note interval, read out via `cursor % 5`,
  resetting to 0 on phrase boundaries (unchanged trigger conditions). Validated as matching real
  charting convention both by name (Clone Hero Wiki's "Ladder" pattern) and by mechanism (Tensor
  Hero paper's independently-derived anchor+motion+wraparound formulation). This is a correctness
  fix, not a stylistic preference — the old version was measurably wrong for any run longer than 5
  notes in one direction.
- 2026-07-21 — **The `_assign_group_lanes` placeholder for a chord's non-anchor lanes (open since
  the same-day contour rework above) is resolved: replaced with a scored-candidate chord-shape
  heuristic** (`_chord_shape_candidates` + `_rank_chord_shape`). This was flagged in that same
  session's "KNOWN UNRESOLVED ISSUE" and in Open Questions as "not a considered decision, chord
  logic explicitly deprioritized" — it is now a considered decision. Chosen approach: enumerate
  every legal way to place a chord's notes on the 5 lanes (there are only `C(5,k)`, ≤10) and rank
  each by how well it continues the phrase's direction, shows harmonic change from (or stability
  against) the previous chord, avoids re-flattening at the floor/ceiling, avoids reusing a shape
  from a couple of chords ago, and reads as a clean (contiguous) shape — highest-ranked wins.
  Rejected alternatives: a hard adjacency rule for chord shapes (explicitly what the prior
  interval-spread voicing did, and explicitly what the collaborator handoff asked NOT to bring
  back — "there should not be exactly one legal representation"); a strategy-pattern/policy-object
  architecture for pluggable chord rules (explicitly rejected by the handoff — keep the existing
  procedural-function style of `mapper.py`, no DI, no plugin architecture). See Current State for
  the full implementation writeup and the honest scope of what the new tests do/don't guarantee.
- 2026-07-21 — **`_rank_chord_shape`'s weights: named module-level constants, not a `ShapeWeights`
  config dataclass.** An external PR review suggested a config-object direction for easier future
  tuning. Took the named-constants half of that suggestion (matches the file's existing
  `_RECENT_SHAPES`/`_TREND_WINDOW` convention, delivers the actual ask — tuning is now editing a
  number, not hunting through scoring logic) but not the dataclass — a config object crosses closer
  into the "policy object" territory the original chord-mapper handoff explicitly asked to avoid,
  and nothing about the current single-caller, single-config-instance usage needs the extra
  indirection. Revisit if a real need for multiple weight profiles (e.g. per-genre tuning) shows up.

---

## 10. Open Questions

- Does TuxGuitar's .gpx→.gp5 conversion preserve mid-bar and linear tempo automations? (M0 answers this.)
- ~~Sheet Happens 7-string tabs (Drop A material): does the open-note rule apply to string 7 fret 0 only, or lowest-two-strings chug clusters?~~ **Resolved 2026-07-20** — fret 0 on the single lowest-tuned string only. See Decision Log.
- GP "let ring" and harmonics flags — ignore or map? (Default: ignore, revisit if charts feel wrong.)
- ~~Does the `score.gpif` XML tempo `<Automation>` schema match what's assumed...~~ **Resolved 2026-07-17** — confirmed against a real file, now automated in `shred2chart/gpif_tempo.py`. See Current State.
- New (2026-07-17): is `<Position>` really a 0..1 fraction of the bar for mid-bar tempo changes? Only verified data point so far is `Position=0` (start of bar). Get a real file with an actual mid-bar tempo automation to confirm — if wrong, `gpif_tempo.dump_tempo_events`'s tick math for such events needs fixing.
- ~~New (2026-07-17): is the whole Sheet Happens library GP7 (zip) format, or a mix of GP6 (`.gpx`) and GP7/8?~~ **Decision made 2026-07-20** — keep BCFS/BCFZ code regardless, pending a full library audit. See Decision Log.
- ~~What do the `<Slide><Flags>` integer values in GPIF actually mean, bit for bit?~~ **Resolved 2026-07-17** — cross-checked against editor-on-fire's GP importer, which documents the GP5+ bitmask (1/2/4/8/16/32). See Current State. **Further confirmed 2026-07-17**: `Still Searching` contains flags {1, 2, 4, 20, 32}; 20 = 16+4 is a bit *combination*, which only works if these are genuine bitmask values. Considered closed.
- New (2026-07-17): which track is "the lead guitar" isn't always obvious — sometimes it's clearly named (`Lead Guitar` vs `Rhythm Guitar`), sometimes it's ambiguous (3 tracks all named `Overdriven Guitar` in `Brag.gp`). `dump-ir --track N` punts this decision to the user for now; Stage 4/M1 wrap-up should decide whether picking a track can be automated (e.g. "most technique flags" or "highest average pitch") or should just always ask.
- New (2026-07-21): step-size bucketing (`_interval_to_step`: semitone gap → lane-step magnitude
  1-4) remains our own unconfirmed heuristic. RBN's "preserve motion over consistency" principle is
  now sourced-confirmed (see Current State) and supports the *general direction* of prioritizing
  flow over literal pitch, but does not specify or confirm any particular step-sizing algorithm.
  A hypothesis that real charters force a flat 1-lane-per-note step for detected monotonic runs
  (3+ notes, same direction), reserving proportional sizing for disjunct/leap motion, is still
  unconfirmed by any source found so far — would need a real chart example or a more explicit
  citation to promote past hypothesis. Not implemented; would require a new run-detection pre-pass
  that doesn't currently exist in `mapper.py`.
- Resolved 2026-07-21: phrase-boundary reset always sets the cursor to 0 (green), regardless of the
  new phrase's direction. Considered settled — real-guitar fretting-hand-anchoring convention (the
  low anchor stays low regardless of phrase direction; higher notes are a temporary reach off that
  anchor) supports the current always-reset-to-green behavior. No change made. Confidence: moderate
  (reasoning is sound but not drawn from a chart-authoring source specifically).
- New (2026-07-21): same-tick lane-collision handling for genuine chords (as opposed to blend-seam
  collisions) is implemented (nearest-free-lane placement) but untested against any real
  chord-bearing file. A real power chord will get whatever two lanes its two pitches' contour
  cursor values land on, nudged apart if they'd collide — could look musically arbitrary on real
  material. Needs a real playtest check. ~~Related: `_assign_group_lanes`'s placement logic for a
  chord's 3rd+ note is an explicit placeholder, not a decision — see Current State. Known-broken
  for open-chug chords (see the `strict=True` xfail test `test_open_chug_chord_keeps_all_notes`).~~
  **Resolved 2026-07-21** — placeholder replaced with a scored chord-shape heuristic; the
  open-chug-chord xfail is fixed as a structural side effect (no longer reads `OPEN_NOTE` as a
  placement seed). See Current State and Decision Log. The "untested against a real
  chord-bearing file" half of this question still stands — no real `.gp` playtest of chord-heavy
  material has happened yet, only synthetic fixtures.
- Closed 2026-07-21, N/A: RBN's Trill/Tremolo lane markers are a Rock Band MIDI-engine-specific
  mechanic with no equivalent in the `.chart` text format this project emits. No action needed.
- New (2026-07-20): `ghost_note` in `ir_gpif.py` is now implemented as `"GhostNote" in props` (inferred from the GPIF property naming pattern), but has not been verified against a real file that carries a ghost note. If a real file turns up and `ghost_note` is always `False` despite visible ghost notes in the tab, check whether GP7 uses a different property name (e.g. at the beat level, or under a different `<Property name=...>` key) and fix accordingly.
- New (2026-07-21), backlog: a dedicated `mapper.py` regression corpus (`single_notes/`, `dyads/`,
  `triads/`, `staircase_runs/`, `repeated_chords/`, `awkward_spreads/` — synthetic IR fixtures each
  paired with expected lane output) was suggested during PR #8 review, so future weight/heuristic
  tweaks to `_rank_chord_shape` can be checked against "did this actually improve something" instead
  of eyeballing individual test assertions. Not built yet — real payoff starts once weight-tuning
  against actual playtest feedback begins, and that in turn needs the still-open "real chord-bearing
  file" playtest above. Revisit together with that.
