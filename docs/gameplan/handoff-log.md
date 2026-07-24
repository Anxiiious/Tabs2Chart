# Agent Handoff Log

The canonical structured handoff database is in Notion:

- [SHRED2CHART Agent Handoff Log](https://app.notion.com/p/b09cf01956b94991a3b368200dec6d54)
- [Tabs2Chart Repo database](https://app.notion.com/p/f05043063a684a74a2cd88c8b0699061)

This file is a local navigation mirror, not a substitute for the database’s structured fields, relations, and rollups.

## Recent structural handoffs

- **2026-07-23 — Alignment guide moved right and lead-in reduced to two bars.** Shifted the MoonScraper overlay 324 pixels right, changed the shared default to two tempo/meter-aware measures, rebuilt both deliverables, and passed 113 tests plus launch/manifest smoke checks. Human real-song visual and Clone Hero verification remain. [Notion entry](https://app.notion.com/p/3a7b82db13b7819bbcacfa1bb94e3301).

- **2026-07-23 — Automatic MoonScraper opening and visual alignment editor delivered.** Added editor discovery, saved auto-open preferences, a manual reopen action, rebuilt the importer, prepared a pinned source patch, and delivered a runnable non-destructive BepInEx custom copy with native-waveform/transient alignment controls. [Notion entry](https://app.notion.com/p/3a7b82db13b781e386edc3e175649fb4).

- **2026-07-23 — Four empty lead-in bars made the default.** Restored and improved the shared lead-in transform, added timing regressions, passed 106 tests, and rebuilt the Windows importer. Human real-song timing confirmation remains. [Notion entry](https://app.notion.com/p/3a6b82db13b78127ae9bf633424d7802).
- **2026-07-23 — Easy Windows tab + song importer completed.** Finished and tested the thin GUI wrapper, built `dist/Tabs2Chart.exe`, normalized album art, and fixed Windows Moon Scraper command parsing. Human real-song/Clone Hero verification remains. [Notion entry](https://app.notion.com/p/3a6b82db13b7813c8ca0de23fcaae9c2).
- **2026-07-23 — Local documentation restructure.** Split the local monolithic Game Plan to mirror the Notion index/subpage/archive model; added a root Agent Protocol and retained the original working copy as a read-only legacy snapshot. [Notion entry](https://app.notion.com/p/3a6b82db13b7815d904de705394133b3).
- **2026-07-23 — Repository/Notion state audit.** Recorded the dirty, diverged local `main`, unverified GUI/package artifacts, and required reconciliation/test steps. [Notion entry](https://app.notion.com/p/3a6b82db13b781a9a241f7c001a30c3e).
- **2026-07-22 — Full Notion project and agent-handoff restructure.** Created the repository index, split Game Plan, Current State archive, structured Handoff Log, relations/rollups, and Agent Protocol. [Notion entry](https://app.notion.com/p/3a6b82db13b781caa74ec7ae305aa468).

## Maintenance

At session close, write the full structured entry to Notion, append the meaningful state change to [current-state.md](current-state.md), and refresh this short list only when it improves local resumption.
