# Agent Handoff Log

The canonical structured handoff database is in Notion:

- [SHRED2CHART Agent Handoff Log](https://app.notion.com/p/b09cf01956b94991a3b368200dec6d54)
- [Tabs2Chart Repo database](https://app.notion.com/p/f05043063a684a74a2cd88c8b0699061)

This file is a local navigation mirror, not a substitute for the database’s structured fields, relations, and rollups.

## Recent structural handoffs

- **2026-07-26 — GUI rebuilt on PySide6.** Replaced the Tk importer window with a Qt one to get real rounded cards, gradients, hover states, and readable type; restructured the layout from one long vertical card stack into header / summary / two-column source+controls / CTA / log / footer. Added `shred2chart/qt_theme.py` and `shred2chart/gui_common.py`; preserved the Tk version verbatim as `shred2chart/gui_tk_legacy.py`. Dropped `tkinterdnd2` (Qt handles file drops) and moved conversion off `queue` polling onto a `QThread` worker. `shred2chart.spec` no longer bundles Tcl/Tk and now trims unused Qt modules. Validation: 128 tests passed; window and per-measure dialog inspected via offscreen renders. **Not done — next session must:** `pip install -e ".[dev]"` (pulls `PySide6-Essentials`), launch on Windows to check Segoe UI metrics/DPI, rebuild `dist/Tabs2Chart.exe` and record its SHA-256, and mirror this entry plus the two new decision-log entries into the canonical Notion pages (the Notion connector was unauthorized during this session, so only the local copies were written).

- **2026-07-26 — Dense chord-cap duplicate repair.** Fixed the `KeyError` observed importing Still Searching with blend tracks `0,2`: an octave/string double referring to a pitch removed by the three-lane cap is now remapped to the retained highest pitch. Exact CLI reproduction succeeds. Validation: 128 tests passed with `--basetemp .test-chord-cap`; rebuilt `dist/Tabs2Chart.exe` (SHA-256 `F1D2301A75F5C836E9982F3B409318FEE8F5D8FAEB4E3074F8DDDCD9C4E9D900`).

- **2026-07-26 — Expressive chord progressions.** Dense twin-guitar chord walls now retain both parts, and any fast sequence with two distinct chord changes in a bar receives non-adjacent dyad/triad voicings where possible; repeated chords remain stable. Still Searching Section H preview uses `G/R/B`, `R/Y/O`, and `R/B/O`. Validation: 127 tests passed with `--basetemp .test-expressive-chords`; rebuilt `dist/Tabs2Chart.exe` (SHA-256 `E3C24DF4E81BBFC03E1FE8A7FCD871B08DB846944C14BBB9655EBB7C3B98CFF9`). Next: user visual/playability pass.

- **2026-07-26 — Local repeated-phrase anchoring.** Retained exact-measure replay and added the narrow case for nearby same-section bar-start phrases with four exact matching events but different tails. The anchor is selected from the closest variation's current measure, so later unrelated variants cannot pull it into a conflicting range. Real Still Searching output: measures 15/16 shared chugs are R, harmony R/Y, and lower chugs G. Validation: 124 tests passed with `--basetemp .test-riff-family`; rebuilt `dist/Tabs2Chart.exe` (SHA-256 `70E98EEBB7ADB64F4C12CE3427BC4E3A93E2394AFF2CC077F4EB459EF762DA9E`). Next: human visual/playability check in MoonScraper.

- **2026-07-26 — Exact-measure lane memory (supersedes four-event riff matcher).** Identical source measures now replay their first lane pattern later in the song. The fingerprint covers every fretted-note onset and its offset within the measure, so partial/similar riffs do not get forced together. Validation: 123 tests passed; rebuilt `dist/Tabs2Chart.exe`.

- **2026-07-26 — Returning-riff lane memory.** Added an exact four-event note-and-rhythm fingerprint so a returning riff anywhere in the tab reuses its first lane pattern, without treating every isolated repeated chord as global. Section chord-shape memory survives rests. Validation: 123 tests passed; rebuilt `dist/Tabs2Chart.exe`.

- **2026-07-26 — Bulk source-measure track selection.** Added an inclusive range apply control to the Advanced per-measure picker (`From`/`through`/track or Auto/`Apply range`), retaining individual measure overrides. Validation: 54 focused tests passed; rebuilt `dist/Tabs2Chart.exe`.

- **2026-07-26 — Windows GUI packaging repair.** Fixed the rebuilt executable's missing-`tkinter` startup crash by explicitly bundling matching Tk source, the `_tkinter` extension, Tcl/Tk DLLs/scripts, and a runtime hook. Rebuilt `dist/Tabs2Chart.exe` (SHA-256 `E9FC10D6E6767E9EA332CE27C16968AE9537C400F91A99503A724502E36EAE6F`) and passed a three-second hidden launch smoke test.

- **2026-07-26 — Per-source-measure track overrides.** Added a GUI picker in Advanced plus service/CLI support (`--measure-tracks 5:2,6:1`). Users can keep `Auto` or force any original GP track per source measure; repeated playback respects the same choice, and a forced silent measure remains silent. Rebuilt `dist/Tabs2Chart.exe`. Validation: 120 tests passed; needs human GUI validation.

- **2026-07-26 — Still Searching harmony/wrap mapping revision.** Replaced complete-bar harmony alternation with active upper-register selection plus a marked wide-dyad preference, yielding `G/R -> R/B` in the requested passage. Added directional phrase headroom at section/rest starts and descending-only bar starts, yielding `R -> G` rather than `G -> O` for the later chug. Regenerated the active dist chart, retained `notes.before-harmony-mapping.chart` as rollback, and added three regressions. Validation: 26 targeted tests and 116 complete tests passed (the latter with a workspace-local pytest base due a permission-blocked default temp directory). Human visual/playability verification remains.

- **2026-07-26 — Custom MoonScraper measure counter added.** Added a live, upper-left `Measure N (tick T)` overlay for every chart opened by either custom-editor delivery path. It uses the same measure-line cadence as upstream rendering; the BepInEx DLL was rebuilt and the pinned source patch applies cleanly. Human validation against a real chart with a meter change remains.

- **2026-07-23 — Alignment guide moved right and lead-in reduced to two bars.** Shifted the MoonScraper overlay 324 pixels right, changed the shared default to two tempo/meter-aware measures, rebuilt both deliverables, and passed 113 tests plus launch/manifest smoke checks. Human real-song visual and Clone Hero verification remain. [Notion entry](https://app.notion.com/p/3a7b82db13b7819bbcacfa1bb94e3301).

- **2026-07-23 — Automatic MoonScraper opening and visual alignment editor delivered.** Added editor discovery, saved auto-open preferences, a manual reopen action, rebuilt the importer, prepared a pinned source patch, and delivered a runnable non-destructive BepInEx custom copy with native-waveform/transient alignment controls. [Notion entry](https://app.notion.com/p/3a7b82db13b781e386edc3e175649fb4).

- **2026-07-23 — Four empty lead-in bars made the default.** Restored and improved the shared lead-in transform, added timing regressions, passed 106 tests, and rebuilt the Windows importer. Human real-song timing confirmation remains. [Notion entry](https://app.notion.com/p/3a6b82db13b78127ae9bf633424d7802).
- **2026-07-23 — Easy Windows tab + song importer completed.** Finished and tested the thin GUI wrapper, built `dist/Tabs2Chart.exe`, normalized album art, and fixed Windows Moon Scraper command parsing. Human real-song/Clone Hero verification remains. [Notion entry](https://app.notion.com/p/3a6b82db13b7813c8ca0de23fcaae9c2).
- **2026-07-23 — Local documentation restructure.** Split the local monolithic Game Plan to mirror the Notion index/subpage/archive model; added a root Agent Protocol and retained the original working copy as a read-only legacy snapshot. [Notion entry](https://app.notion.com/p/3a6b82db13b7815d904de705394133b3).
- **2026-07-23 — Repository/Notion state audit.** Recorded the dirty, diverged local `main`, unverified GUI/package artifacts, and required reconciliation/test steps. [Notion entry](https://app.notion.com/p/3a6b82db13b781a9a241f7c001a30c3e).
- **2026-07-22 — Full Notion project and agent-handoff restructure.** Created the repository index, split Game Plan, Current State archive, structured Handoff Log, relations/rollups, and Agent Protocol. [Notion entry](https://app.notion.com/p/3a6b82db13b781caa74ec7ae305aa468).

## Maintenance

At session close, write the full structured entry to Notion, append the meaningful state change to [current-state.md](current-state.md), and refresh this short list only when it improves local resumption.
