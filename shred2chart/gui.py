"""Small desktop importer for turning a Guitar Pro tab + recording into a
ready-to-scan Clone Hero song folder.

Run unpackaged with `python -m shred2chart.gui`, or via the installed
`shred2chart-gui` console script. For a standalone .exe, package this
module's `main()` with PyInstaller (see packaging/gui_entry.py).
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

from . import media
from .cli import ConvertError, convert_song, peek_metadata
from .moonscraper import MoonscraperLaunchError, find_moonscraper, open_chart

_GP_SUFFIXES = {".gp", ".gpx", ".gp3", ".gp4", ".gp5"}
_GP_FILETYPES = [
    ("Guitar Pro files", "*.gp *.gpx *.gp3 *.gp4 *.gp5"),
    ("All files", "*.*"),
]
_AUDIO_FILETYPES = [
    ("Audio files", " ".join(f"*{ext}" for ext in sorted(media.AUDIO_EXTENSIONS))),
    ("All files", "*.*"),
]
_IMAGE_FILETYPES = [
    ("Image files", " ".join(f"*{ext}" for ext in sorted(media.IMAGE_EXTENSIONS))),
    ("All files", "*.*"),
]
_MOONSCRAPER_FILETYPES = [
    ("MoonScraper Chart Editor", "Moonscraper Chart Editor.exe"),
    ("Windows applications", "*.exe"),
]

_CONFIG_PATH = Path.home() / ".shred2chart" / "gui_config.json"

_AppBase = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk


def _parse_dnd_path(data: str) -> str:
    """Return the first path from a Tk DND payload.

    Tk wraps paths containing spaces in braces and may send several paths in
    one payload.  The importer intentionally accepts only the first.
    """
    data = data.strip()
    match = re.match(r'^(?:\{([^}]*)\}|"([^"]*)"|(\S+))', data)
    if not match:
        return data
    return next(group for group in match.groups() if group is not None)


def _suggest_companion_files(gp_file: str | Path) -> tuple[Path | None, Path | None]:
    """Find same-folder audio and artwork that likely belong to *gp_file*."""
    path = Path(gp_file)
    if not path.is_file():
        return None, None

    audio = next(
        (
            path.with_suffix(ext)
            for ext in sorted(media.AUDIO_EXTENSIONS)
            if path.with_suffix(ext).is_file()
        ),
        None,
    )
    art = next(
        (
            path.with_suffix(ext)
            for ext in sorted(media.IMAGE_EXTENSIONS)
            if path.with_suffix(ext).is_file()
        ),
        None,
    )
    if art is None:
        for name in ("cover", "folder", "album"):
            art = next(
                (
                    path.parent / f"{name}{ext}"
                    for ext in sorted(media.IMAGE_EXTENSIONS)
                    if (path.parent / f"{name}{ext}").is_file()
                ),
                None,
            )
            if art is not None:
                break
    return audio, art


def _song_output_dir(root: str | Path, artist: str, title: str) -> Path:
    """Build the final song folder beneath a user-selected Songs directory."""
    from .cli import _safe_path_part  # noqa: PLC0415

    return Path(root).expanduser() / f"{_safe_path_part(artist)} - {_safe_path_part(title)}"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_config(config: dict) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
    except OSError:
        pass


class App(_AppBase):
    def __init__(self):
        super().__init__()
        self.title("Tabs2Chart Importer")
        self.geometry("720x710")
        self.minsize(640, 520)

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._out_dir: Path | None = None
        self._converting = False
        self._config = _load_config()

        self.gp_file = tk.StringVar()
        self.audio_file = tk.StringVar()
        self.album_art_file = tk.StringVar()
        self.out_dir_var = tk.StringVar()
        self.out_preview_var = tk.StringVar(value="Choose a tab to preview the imported song.")
        self.metadata_var = tk.StringVar(value="No tab selected")
        self.track = tk.StringVar()
        self.tracks = tk.StringVar()
        self.offset_ms = tk.StringVar(value="0")
        discovered_moonscraper = find_moonscraper(self._config.get("moonscraper_exe"))
        self.moonscraper_exe = tk.StringVar(
            value=str(discovered_moonscraper) if discovered_moonscraper else ""
        )
        self.open_after_import = tk.BooleanVar(
            value=bool(self._config.get("open_in_moonscraper", True))
        )

        self.gp_file.trace_add("write", lambda *_: self._update_preview())
        self.out_dir_var.trace_add("write", lambda *_: self._update_preview())

        self._build_widgets()
        if not _DND_AVAILABLE:
            self._log("(tip: install 'tkinterdnd2' to enable drag-and-drop file support)")
        self.after(100, self._poll_log_queue)

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="1. Guitar Pro tab:").grid(row=0, column=0, sticky="w", **pad)
        gp_entry = ttk.Entry(main, textvariable=self.gp_file)
        gp_entry.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse...", command=self._pick_gp_file).grid(row=0, column=2, **pad)
        self._register_drop(gp_entry, self.gp_file, kind="gp")

        ttk.Label(main, text="2. Song audio:").grid(row=1, column=0, sticky="w", **pad)
        audio_entry = ttk.Entry(main, textvariable=self.audio_file)
        audio_entry.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse...", command=self._pick_audio_file).grid(row=1, column=2, **pad)
        self._register_drop(audio_entry, self.audio_file, kind="audio")

        ttk.Label(main, text="3. Clone Hero Songs folder:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(main, textvariable=self.out_dir_var).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse...", command=self._pick_out_dir).grid(row=2, column=2, **pad)

        summary = ttk.LabelFrame(main, text="Import summary")
        summary.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        ttk.Label(summary, textvariable=self.metadata_var, font=("", 11, "bold")).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        ttk.Label(summary, textvariable=self.out_preview_var, foreground="#555", wraplength=650).pack(
            anchor="w", padx=10, pady=(0, 8)
        )

        self.convert_btn = ttk.Button(main, text="Import tab + song", command=self._on_convert)
        self.convert_btn.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))

        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))

        advanced = ttk.LabelFrame(main, text="Advanced (optional)")
        advanced.grid(row=6, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        advanced.columnconfigure(1, weight=1)

        ttk.Label(advanced, text="Album art:").grid(row=1, column=0, sticky="w", **pad)
        art_entry = ttk.Entry(advanced, textvariable=self.album_art_file)
        art_entry.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(advanced, text="Browse...", command=self._pick_album_art).grid(row=1, column=2, **pad)
        self._register_drop(art_entry, self.album_art_file, kind="image")

        ttk.Label(advanced, text="Track (verbatim, skip blending):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.track, width=8).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Tracks to blend (e.g. 1,0):").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.tracks, width=12).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Audio offset (ms):").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.offset_ms, width=8).grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="MoonScraper app:").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.moonscraper_exe).grid(
            row=5, column=1, sticky="ew", **pad
        )
        ttk.Button(
            advanced, text="Browse...", command=self._pick_moonscraper
        ).grid(row=5, column=2, **pad)
        ttk.Checkbutton(
            advanced,
            text="Open the generated chart in MoonScraper after import",
            variable=self.open_after_import,
            command=self._save_moonscraper_preferences,
        ).grid(row=6, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(main, text="Progress:").grid(row=7, column=0, sticky="w", padx=8)
        log_frame = ttk.Frame(main)
        log_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 8))
        main.rowconfigure(8, weight=1)

        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled",
                                 yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        action_frame = ttk.Frame(main)
        action_frame.grid(row=9, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))
        action_frame.columnconfigure((0, 1), weight=1)
        self.open_folder_btn = ttk.Button(
            action_frame, text="Open output folder", command=self._open_output_folder,
            state="disabled"
        )
        self.open_folder_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.open_moonscraper_btn = ttk.Button(
            action_frame, text="Open in MoonScraper", command=self._open_in_moonscraper,
            state="disabled"
        )
        self.open_moonscraper_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self._restore_last_folders()

    def _register_drop(self, widget: tk.Widget, var: tk.StringVar, kind: str) -> None:
        if not _DND_AVAILABLE:
            return
        widget.drop_target_register(DND_FILES)

        def on_drop(event) -> None:
            path = _parse_dnd_path(event.data)
            suffix = Path(path).suffix.lower()
            accepted = {
                "gp": _GP_SUFFIXES,
                "audio": media.AUDIO_EXTENSIONS,
                "image": media.IMAGE_EXTENSIONS,
            }[kind]
            if suffix not in accepted:
                self._log(f"Ignored {Path(path).name}: not a supported {kind} file.")
                return
            var.set(path)
            if kind == "gp":
                self._on_gp_selected(path)

        widget.dnd_bind("<<Drop>>", on_drop)

    def _restore_last_folders(self) -> None:
        last_gp_dir = self._config.get("last_gp_dir")
        if last_gp_dir:
            self._last_gp_dir = last_gp_dir
        last_audio_dir = self._config.get("last_audio_dir")
        if last_audio_dir:
            self._last_audio_dir = last_audio_dir
        last_out_dir = self._config.get("last_out_dir")
        if last_out_dir:
            self._last_out_dir = last_out_dir
            self.out_dir_var.set(last_out_dir)
        else:
            default_root = Path.cwd() / "songs"
            self._last_out_dir = str(default_root)
            self.out_dir_var.set(str(default_root))

    def _update_preview(self) -> None:
        gp_file = self.gp_file.get().strip()
        if not gp_file or not Path(gp_file).is_file():
            self.metadata_var.set("No tab selected")
            self.out_preview_var.set("Choose a tab to preview the imported song.")
            return
        try:
            artist, title = peek_metadata(gp_file)
        except Exception as exc:
            self.metadata_var.set("Could not read this tab")
            self.out_preview_var.set(str(exc))
            return
        self.metadata_var.set(f"{artist} — {title}")
        root = self.out_dir_var.get().strip() or str(Path.cwd() / "songs")
        self.out_preview_var.set(f"Ready to import into: {_song_output_dir(root, artist, title)}")

    def _on_gp_selected(self, path: str) -> None:
        """Populate obvious companion files without replacing user choices."""
        audio, art = _suggest_companion_files(path)
        if audio is not None and not self.audio_file.get().strip():
            self.audio_file.set(str(audio))
            self._log(f"Matched song audio: {audio.name}")
        if art is not None and not self.album_art_file.get().strip():
            self.album_art_file.set(str(art))
            self._log(f"Matched album art: {art.name}")

    def _pick_gp_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a GP file", filetypes=_GP_FILETYPES,
            initialdir=getattr(self, "_last_gp_dir", None),
        )
        if path:
            self.gp_file.set(path)
            self._on_gp_selected(path)
            self._last_gp_dir = str(Path(path).parent)
            self._config["last_gp_dir"] = self._last_gp_dir
            _save_config(self._config)

    def _pick_audio_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an audio file", filetypes=_AUDIO_FILETYPES,
            initialdir=getattr(self, "_last_audio_dir", None),
        )
        if path:
            self.audio_file.set(path)
            self._last_audio_dir = str(Path(path).parent)
            self._config["last_audio_dir"] = self._last_audio_dir
            _save_config(self._config)

    def _pick_album_art(self) -> None:
        path = filedialog.askopenfilename(title="Choose album art", filetypes=_IMAGE_FILETYPES)
        if path:
            self.album_art_file.set(path)

    def _pick_out_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Choose an output folder", initialdir=getattr(self, "_last_out_dir", None),
        )
        if path:
            self.out_dir_var.set(path)
            self._last_out_dir = path
            self._config["last_out_dir"] = path
            _save_config(self._config)
            self._update_preview()

    def _pick_moonscraper(self) -> str | None:
        path = filedialog.askopenfilename(
            title="Choose Moonscraper Chart Editor.exe",
            filetypes=_MOONSCRAPER_FILETYPES,
            initialdir=str(Path(self.moonscraper_exe.get()).parent)
            if self.moonscraper_exe.get().strip()
            else None,
        )
        if not path:
            return None
        self.moonscraper_exe.set(path)
        self._save_moonscraper_preferences()
        return path

    def _save_moonscraper_preferences(self) -> None:
        self._config["moonscraper_exe"] = self.moonscraper_exe.get().strip()
        self._config["open_in_moonscraper"] = self.open_after_import.get()
        _save_config(self._config)

    def _log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self._log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _open_output_folder(self) -> None:
        if self._out_dir and self._out_dir.exists():
            os.startfile(self._out_dir)  # noqa: S606 - Windows-only, user-chosen local path

    def _open_in_moonscraper(self, *, prompt_if_missing: bool = True) -> bool:
        if self._out_dir is None:
            return False
        chart_path = self._out_dir / "notes.chart"
        executable = find_moonscraper(self.moonscraper_exe.get().strip())
        if executable is None and prompt_if_missing:
            selected = self._pick_moonscraper()
            executable = find_moonscraper(selected)
        if executable is None:
            self._log("MoonScraper was not opened: choose its executable under Advanced.")
            return False

        self.moonscraper_exe.set(str(executable))
        self._save_moonscraper_preferences()
        try:
            open_chart(
                chart_path,
                executable,
                manifest_path=self._out_dir / "moon-scraper-manifest.json",
            )
        except MoonscraperLaunchError as exc:
            self._log(f"MoonScraper was not opened: {exc}")
            messagebox.showwarning("MoonScraper", str(exc))
            return False
        self._log(f"Opened notes.chart in MoonScraper: {executable}")
        return True

    def _on_convert(self) -> None:
        if self._converting:
            return
        gp_file = self.gp_file.get().strip()
        if not gp_file:
            messagebox.showerror("Tabs2Chart", "Choose a Guitar Pro tab first.")
            return
        if Path(gp_file).suffix.lower() not in _GP_SUFFIXES:
            messagebox.showerror("Tabs2Chart", "Choose a .gp, .gpx, .gp3, .gp4, or .gp5 tab.")
            return
        audio = self.audio_file.get().strip()
        if not audio:
            if not messagebox.askyesno(
                "Import without audio?",
                "No song audio is selected. The chart will be created, but Clone Hero cannot play it "
                "until you add song.ogg.\n\nContinue anyway?",
            ):
                return
        elif not Path(audio).is_file():
            messagebox.showerror("Tabs2Chart", f"Song audio does not exist:\n{audio}")
            return

        def parse_int(var: tk.StringVar, default: int) -> int:
            text = var.get().strip()
            return int(text) if text else default

        try:
            offset_ms = parse_int(self.offset_ms, 0)
            track = int(self.track.get().strip()) if self.track.get().strip() else None
        except ValueError:
            messagebox.showerror("Tabs2Chart", "Track and audio offset must be whole numbers.")
            return

        try:
            artist, title = peek_metadata(gp_file)
        except Exception as exc:
            messagebox.showerror("Tabs2Chart", f"Could not read the tab:\n{exc}")
            return
        root = self.out_dir_var.get().strip() or str(Path.cwd() / "songs")
        out_dir = _song_output_dir(root, artist, title)
        if out_dir.exists() and any(out_dir.iterdir()):
            if not messagebox.askyesno(
                "Replace existing import?",
                f"This song folder already contains files:\n{out_dir}\n\n"
                "Replace the generated chart files and keep any other files?",
            ):
                return

        kwargs = dict(
            gp_file=gp_file,
            out=out_dir,
            audio=audio or None,
            album_art=self.album_art_file.get().strip() or None,
            track=track,
            tracks=self.tracks.get().strip() or None,
            offset_ms=offset_ms,
        )

        self._converting = True
        self.convert_btn.configure(state="disabled", text="Importing...")
        self.open_folder_btn.configure(state="disabled")
        self.open_moonscraper_btn.configure(state="disabled")
        self.progress.start(12)
        self._out_dir = None
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        thread = threading.Thread(target=self._run_convert, kwargs=kwargs, daemon=True)
        thread.start()

    def _run_convert(self, **kwargs) -> None:
        try:
            result = convert_song(on_progress=self._log_queue.put, **kwargs)
        except ConvertError as e:
            self._log_queue.put(f"error: {e}")
            self.after(0, self._on_convert_done, None)
        except Exception as e:  # unexpected failure - surface it, don't crash the window
            self._log_queue.put(f"unexpected error: {e}")
            self.after(0, self._on_convert_done, None)
        else:
            self.after(0, self._on_convert_done, result.out_dir)

    def _on_convert_done(self, out_dir: Path | None) -> None:
        self._converting = False
        self.progress.stop()
        self.convert_btn.configure(state="normal", text="Import tab + song")
        if out_dir is not None:
            self._out_dir = out_dir
            self.open_folder_btn.configure(state="normal")
            self.open_moonscraper_btn.configure(state="normal")
            opened = False
            if self.open_after_import.get():
                opened = self._open_in_moonscraper()
            messagebox.showinfo(
                "Import complete",
                f"Clone Hero song created successfully:\n\n{out_dir}\n\n"
                + (
                    "The generated chart is now open in MoonScraper."
                    if opened
                    else "Use Open in MoonScraper to review it, then scan songs in Clone Hero."
                ),
            )
        else:
            messagebox.showerror(
                "Import failed",
                "The import did not finish. See the progress log for the exact error.",
            )


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
