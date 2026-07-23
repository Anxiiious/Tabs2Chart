"""Drag-and-drop GUI for shred2chart: pick a GP file and an audio file,
click Convert, get a Clone Hero song folder out - no terminal required.
See SHRED2CHART_GAMEPLAN.md section 11 (M6) for the design context this
was built against.

Run unpackaged with `python -m shred2chart.gui`, or via the installed
`shred2chart-gui` console script. For a standalone .exe, package this
module's `main()` with PyInstaller (see packaging/gui_entry.py).
"""
from __future__ import annotations

import json
import os
import queue
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

_GP_FILETYPES = [("Guitar Pro files", "*.gp *.gpx"), ("All files", "*.*")]
_AUDIO_FILETYPES = [
    ("Audio files", " ".join(f"*{ext}" for ext in sorted(media.AUDIO_EXTENSIONS))),
    ("All files", "*.*"),
]
_IMAGE_FILETYPES = [
    ("Image files", " ".join(f"*{ext}" for ext in sorted(media.IMAGE_EXTENSIONS))),
    ("All files", "*.*"),
]

_CONFIG_PATH = Path.home() / ".shred2chart" / "gui_config.json"

_AppBase = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk


def _parse_dnd_path(data: str) -> str:
    """tkinterdnd2 wraps paths with spaces in {curly braces}; strip that."""
    data = data.strip()
    if data.startswith("{") and data.endswith("}"):
        data = data[1:-1]
    return data


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
        self.title("shred2chart")
        self.geometry("640x560")
        self.minsize(560, 440)

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._out_dir: Path | None = None
        self._converting = False
        self._config = _load_config()

        self.gp_file = tk.StringVar()
        self.audio_file = tk.StringVar()
        self.album_art_file = tk.StringVar()
        self.out_dir_var = tk.StringVar()
        self.out_preview_var = tk.StringVar(value="Output folder will be shown here once a GP file is chosen.")
        self.track = tk.StringVar()
        self.tracks = tk.StringVar()
        self.lead_in_bars = tk.StringVar(value="2")
        self.offset_ms = tk.StringVar(value="0")

        self.gp_file.trace_add("write", lambda *_: self._update_preview())

        self._build_widgets()
        if not _DND_AVAILABLE:
            self._log("(tip: install 'tkinterdnd2' to enable drag-and-drop file support)")
        self.after(100, self._poll_log_queue)

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="GP file (.gp/.gpx):").grid(row=0, column=0, sticky="w", **pad)
        gp_entry = ttk.Entry(main, textvariable=self.gp_file)
        gp_entry.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse...", command=self._pick_gp_file).grid(row=0, column=2, **pad)
        self._register_drop(gp_entry, self.gp_file, kind="gp")

        ttk.Label(main, text="Audio file (optional):").grid(row=1, column=0, sticky="w", **pad)
        audio_entry = ttk.Entry(main, textvariable=self.audio_file)
        audio_entry.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse...", command=self._pick_audio_file).grid(row=1, column=2, **pad)
        self._register_drop(audio_entry, self.audio_file, kind="audio")

        ttk.Label(main, textvariable=self.out_preview_var, foreground="#555").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4)
        )

        self.convert_btn = ttk.Button(main, text="Convert", command=self._on_convert)
        self.convert_btn.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(4, 4))

        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))

        advanced = ttk.LabelFrame(main, text="Advanced (optional)")
        advanced.grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        advanced.columnconfigure(1, weight=1)

        ttk.Label(advanced, text="Output folder (blank = auto \"songs/Band - Song\"):").grid(
            row=0, column=0, sticky="w", **pad
        )
        ttk.Entry(advanced, textvariable=self.out_dir_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(advanced, text="Browse...", command=self._pick_out_dir).grid(row=0, column=2, **pad)

        ttk.Label(advanced, text="Album art:").grid(row=1, column=0, sticky="w", **pad)
        art_entry = ttk.Entry(advanced, textvariable=self.album_art_file)
        art_entry.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(advanced, text="Browse...", command=self._pick_album_art).grid(row=1, column=2, **pad)
        self._register_drop(art_entry, self.album_art_file, kind="image")

        ttk.Label(advanced, text="Track (verbatim, skip blending):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.track, width=8).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Tracks to blend (e.g. 1,0):").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.tracks, width=12).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Lead-in bars:").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.lead_in_bars, width=8).grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Offset (ms):").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.offset_ms, width=8).grid(row=5, column=1, sticky="w", **pad)

        ttk.Label(main, text="Log:").grid(row=6, column=0, sticky="w", padx=8)
        log_frame = ttk.Frame(main)
        log_frame.grid(row=7, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 8))
        main.rowconfigure(7, weight=1)

        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled",
                                 yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        self.open_folder_btn = ttk.Button(
            main, text="Open output folder", command=self._open_output_folder, state="disabled"
        )
        self.open_folder_btn.grid(row=8, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))

        self._restore_last_folders()

    def _register_drop(self, widget: tk.Widget, var: tk.StringVar, kind: str) -> None:
        if not _DND_AVAILABLE:
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda event: var.set(_parse_dnd_path(event.data)))

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

    def _update_preview(self) -> None:
        gp_file = self.gp_file.get().strip()
        if not gp_file or not Path(gp_file).is_file():
            self.out_preview_var.set("Output folder will be shown here once a GP file is chosen.")
            return
        try:
            artist, title = peek_metadata(gp_file)
        except Exception:
            self.out_preview_var.set("Output folder will be shown here once a GP file is chosen.")
            return
        if self.out_dir_var.get().strip():
            return
        from .cli import _safe_path_part  # noqa: PLC0415
        self.out_preview_var.set(f"Will write to: songs/{_safe_path_part(artist)} - {_safe_path_part(title)}")

    def _pick_gp_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a GP file", filetypes=_GP_FILETYPES,
            initialdir=getattr(self, "_last_gp_dir", None),
        )
        if path:
            self.gp_file.set(path)
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

    def _on_convert(self) -> None:
        if self._converting:
            return
        gp_file = self.gp_file.get().strip()
        if not gp_file:
            messagebox.showerror("shred2chart", "Choose a GP file first.")
            return

        def parse_int(var: tk.StringVar, default: int) -> int:
            text = var.get().strip()
            return int(text) if text else default

        try:
            lead_in_bars = parse_int(self.lead_in_bars, 2)
            offset_ms = parse_int(self.offset_ms, 0)
            track = int(self.track.get().strip()) if self.track.get().strip() else None
        except ValueError:
            messagebox.showerror("shred2chart", "Track/lead-in/offset must be whole numbers.")
            return

        kwargs = dict(
            gp_file=gp_file,
            out=self.out_dir_var.get().strip() or None,
            audio=self.audio_file.get().strip() or None,
            album_art=self.album_art_file.get().strip() or None,
            track=track,
            tracks=self.tracks.get().strip() or None,
            lead_in_bars=lead_in_bars,
            offset_ms=offset_ms,
        )

        self._converting = True
        self.convert_btn.configure(state="disabled", text="Converting...")
        self.open_folder_btn.configure(state="disabled")
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
        self.convert_btn.configure(state="normal", text="Convert")
        if out_dir is not None:
            self._out_dir = out_dir
            self.open_folder_btn.configure(state="normal")


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
