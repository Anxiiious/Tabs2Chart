# Architecture & Design

> Local mirror of the [authoritative Notion page](https://app.notion.com/p/3a5b82db13b78145a163c1235deb0c2a). Keep both copies synchronized when project state changes.

## 1. Project Goal
Convert Sheet Happens official Guitar Pro tabs into playable Clone Hero charts (.chart + song.ini), preserving the tab's tempo map so output is rhythm-synced to the real recording out of the box. In practice this means: **GP7 ****`.gp`**** files (zip + GPIF), parsed directly**, as the primary case; **legacy ****`.gp3`****/****`.gp4`****/****`.gp5`**** via PyGuitarPro**; and **GP6 ****`.gpx`**** (BCFS/BCFZ)** retained for compatibility. See §3 for the current ingest architecture.
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
```javascript
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
## 3. Stage 1 — Ingest
**Current implementation (primary path):** real Sheet Happens tabs are **GP7/8 ****`.gp`**** files — plain zip archives** containing a `score.gpif` XML. `gpx_reader.py` extracts `score.gpif` directly via stdlib `zipfile` (one line, no external tools), and `gpif_tempo.py`/`ir_gpif.py` parse tempo, time signatures, sections, and per-note data straight out of that XML. This is a direct, lossless parse — no Guitar Pro or TuxGuitar dependency, no conversion step.
**Legacy formats:** `.gp3`/`.gp4`/`.gp5` files are parsed via **PyGuitarPro** (`tempo.py`/`ir_gp.py`), dispatched by file extension in `cli.py`. This path predates the GP7 finding and remains in active use for any non-GP7 file the user has.
**GP6 ****`.gpx`**** compatibility:** `.gpx` is a genuinely different, older container — proprietary BCFS (binary file system), optionally BCFZ-compressed, **not a zip**. `gpx_reader.py` includes a from-scratch BCFS/BCFZ reader (reverse-engineered from [github.com/Antti/rust-gpx-reader's](http://github.com/Antti/rust-gpx-reader's) writeup, since no official spec exists) that also extracts `score.gpif`, feeding the same GPIF parsers as the GP7 path. This code is retained for compatibility — see Decision Log, 2026-07-20 — but has never been exercised against a real `.gpx` file; every real file seen so far has been GP7 zip format.
### Historical context: Route A vs. Route B
The project originally planned around two candidate routes for the `.gpx` problem, before any real files were in hand:
- **Route A** (prototype path, chosen first): batch-convert `.gpx` → `.gp5` externally via Guitar Pro/TuxGuitar, then parse with PyGuitarPro. Gated on a mandatory verification step (diff tempo events pre/post conversion) that never needed to run in practice.
- **Route B** (fallback / v1.1): direct BCFS/BCFZ parsing, promoted only if Route A dropped tempo data.
Once real files turned out to be GP7 zips rather than GP6 `.gpx`, this distinction stopped mattering for the primary case — direct GPIF parsing (the Route B approach) became the actual implementation, and the GP6 BCFS/BCFZ reader (the other half of Route B) was kept for compatibility rather than promoted or deleted. See Decision Log, 2026-07-22, for the formal record of this transition.
---
## 4. Stage 2 & 3 — IR and SyncTrack
### Intermediate representation
Everything normalizes to a tick-based event list BEFORE mapping decisions. **The IR itself runs at 960 ticks per quarter note** (PyGuitarPro's native convention — `IR_TICKS_PER_QUARTER` in `mapper.py`), not the .chart standard of 192. Stage 5 divides IR ticks by 5 on emit (`_to_chart_ticks`) to produce the .chart file's `Resolution = 192`. This was originally planned as "192 internally, straight dump on emit"; the actual implementation kept PyGuitarPro's 960 internally instead and does the /5 conversion at the Stage 4/5 boundary — documented here to match the code (see `mapper.py`, `chart_writer.py`).
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
- **Global audio offset:** treat three time origins as separate concepts: **chart tick 0**, **first musical event in the GP score**, and **audible start of the recording**. Do not shift note/tempo events just to make the first note land at tick 0; preserve the GP score's pickup, rests, and count-in structure in chart time. Use a single song-level audio calibration value (`Offset` in `.chart` / `delay` in `song.ini`, exposed as `--offset-ms`) only to align the recording waveform with the already-correct SyncTrack.
- **Recommended calibration procedure:** emit the chart with the GP timeline unchanged and offset 0; place the real audio in the song folder; in Moonscraper, find the earliest clearly transient, tempo-locked event (count-in click, drum hit, or first guitar attack—not necessarily the first charted guitar note); adjust the global offset until that transient aligns with its expected chart position; then verify at a late-song marker. If early alignment is correct but late alignment drifts, the problem is the tempo map/tick math, not the global offset.
- **Leading silence and negative offsets:** audio files may contain encoder delay, mastering silence, or a pickup before the first charted note. Permit both positive and negative offset values rather than trimming the chart or deleting intentional rests. Normalize the sign convention in one place and cover it with a round-trip test so `.chart` `Offset`, `song.ini` `delay`, and `--offset-ms` cannot silently disagree.
- **Chart-start policy:** generated charts include **two empty lead-in measures by default**. The starting tempo/time signature are copied to tick 0 so the pre-roll grid uses the song's real meter and speed; the original tempo events, notes, and section markers then move together by exactly two measures. The unchanged audio receives the equivalent negative delay so its original downbeat remains aligned with the shifted score. `--lead-in-bars 0` disables the pre-roll for exceptional cases. Never translate individual event classes independently.
- **Validation gate:** verify synchronization at two or more distant points in the song. A constant error at every point indicates a global audio offset issue; an error that grows or changes direction indicates tempo automation, time-signature inheritance, pickup-bar handling, or tick conversion is wrong. Do not compensate for drift by choosing a compromise offset.
---
## 5. Stage 4 — Note Mapping (the actual hard part)
Core principle: this is **lossy compression, not translation**. Optimize for "feels like the riff," not literal encoding.
### Rules, in priority order
1. **Open-string chugs → CH open note** (`N 7`). Any note on the lowest string at fret 0 (or the primary chug pitch in drop tunings) maps to open. Palm-muted low-string runs are the bread and butter of the target repertoire.
2. **Pitch contour, not absolute pitch.** Sliding window (start: ~2 bars) tracks relative melodic motion. Riff ascends → lanes ascend. Reset/re-center window at phrase boundaries (rests ≥ 1 beat, or section markers from the GP file). Absolute mapping is forbidden — a 1-octave riff and a 3-octave solo both have to fit 5 lanes.
3. **Chords by interval spread:** power chords (root+5th) → two adjacent lanes. Wider voicings (root+octave+, 3+ note chords) → wider lane spread, max 3 lanes for playability. Chord root follows the contour rule; spread is relative to root. *(Superseded — see Decision Log 2026-07-21: replaced by scored chord-shape heuristic.)*
4. **Repeated notes stay on the same lane.** Do not jitter identical consecutive pitches across lanes.
5. **Techniques → CH mechanics:**
	- hammer-on / pull-off flags → forced HOPO (`N 5` flag)
	- tap flag → tap note (`N 6` flag)
	- fast slides → trill-style lane walk; slow slides → sustain on origin note
	- tremolo picking → keep as individual notes (CH players expect the strum wall)
	- dead notes / ghost notes → same lane as neighboring context, no special marking (v1)
6. **Sustain threshold:** notes shorter than ~1/8 at local tempo get zero sustain (CH convention). Longer → sustain = duration_ticks, trimmed to leave a gap before the next note on that lane.
### Tuning knobs (expose as config, tune against real output)
contour window size, phrase-boundary rest threshold, max chord width, sustain cutoff, HOPO auto-threshold distance.
### Quality bar
Output should be playable and *recognizable* without manual edits; Moonscraper is a polish pass, not a rescue pass.
---
## 6. Stage 5 — Emit
- **.chart format, not notes.mid.** Plain text, trivially writable, Moonscraper-native. Sections: `[Song]`, `[SyncTrack]`, `[Events]` (section names from GP markers — free flavor, do it), `[ExpertSingle]`.
- **song.ini** alongside: name, artist, charter, delay, diff_guitar, song_length.
- Output a complete CH song folder. Audio file placement is the user's job (drop song.ogg in the folder); tool prints a reminder.
- Reference spec: the community .chart format doc (Moonscraper repo / FireFox's .chart spec). Agent: fetch and pin the exact `N` flag semantics before writing the emitter — do not code note flags from memory.

---

## 7. Desktop Import Wrapper

The Windows importer is a thin Tkinter/PyInstaller shell over
`cli.convert_song`; it does not duplicate parsing, mapping, timing, or emit
logic. The normal workflow is tab + audio + Clone Hero Songs directory, with
metadata-derived `Artist - Title` output, automatic same-name audio/art
detection, guarded replacement of an existing song folder, background
conversion, progress reporting, and an open-output action. The standalone
`dist/Tabs2Chart.exe` bundles ffmpeg and optional drag/drop support. CLI and
GUI must continue to share the same conversion service and validation path.
After conversion, the wrapper discovers a configured/installed stock
MoonScraper executable and launches it with both
`--tabs2chart-manifest <manifest>` and the `notes.chart` path. Stock
MoonScraper ignores the custom flag and loads the chart argument; the
Tabs2Chart fork consumes the manifest and activates its alignment guide.
Failure to locate or launch the editor is a non-fatal post-conversion warning.

The custom editor is maintained as a small reproducible patch over a pinned
official MoonScraper revision, not as a copied source tree. Its initial
extension automatically enables MoonScraper's native waveform and overlays the
first playable-note time, a detected first audio attack, their live
millisecond delta, one-click offset application, and ±10/±100 ms adjustment.
The heuristic is a guide, not authoritative onset detection: users must still
verify a musically meaningful early marker and a late-song marker.
Until the legacy Unity editor is available for a source build, the same guide
ships in a separate copy of the installed 64-bit MoonScraper through BepInEx 5.
This does not modify the system installation. The adjacent
`dist/Moonscraper-Tabs2Chart` copy is preferred automatically, while the
official-source patch remains the long-term fork implementation.
