# Tabs2Chart MoonScraper fork

This is a reproducible patch layer over the official MoonScraper source, pinned
to upstream revision `cb4c7a8c95f9e09f73ea6c2878b3a7ce5e0baeb0`.

The patch adds:

- `--tabs2chart-manifest <moon-scraper-manifest.json>` startup support;
- a draggable audio-alignment guide that automatically enables MoonScraper's
  waveform, shows the first playable-note time and detected audio attack,
  reports their millisecond delta, applies the detected offset in one click,
  and provides ±10/±100 ms fine-adjustment buttons (`F8` hides/shows it);
- a batch-mode Windows x64 build target that does not require the interactive
  Unity save-folder dialog.

Prepare and build from PowerShell:

```powershell
.\tools\moonscraper-custom\Prepare-MoonscraperFork.ps1
.\tools\moonscraper-custom\Build-MoonscraperFork.ps1
```

The build requires Unity `2018.4.23f1`, the exact version declared by the
upstream project. The official source includes its Windows runtime dependencies
and Unity security patcher. MoonScraper is BSD-3-Clause licensed; retain its
license and attribution files with any redistributed custom binary. The BASS
dependency is free for non-commercial use but requires its own license for
commercial distribution.

`Tabs2ChartAlignmentPlugin.cs` is the same guide packaged as a BepInEx runtime
plugin. It allows a separate modded copy of the installed MoonScraper release
to run the guide without waiting for the legacy Unity editor; the official
source patch remains the long-term fork implementation.

To reproduce the immediately runnable modded copy:

```powershell
.\tools\moonscraper-custom\Build-ModdedCopy.ps1
```

It copies (never modifies) the installed 64-bit MoonScraper, adds the current
official BepInEx 5 Windows x64 runtime, and compiles the guide into
`dist\Moonscraper-Tabs2Chart\BepInEx\plugins`. Tabs2Chart automatically prefers
that adjacent custom copy unless the user explicitly saved another executable.
