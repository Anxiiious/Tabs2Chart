"""LEGACY Tk importer — superseded by the PySide6 front end in gui.py.

This is a verbatim copy of the last Tk implementation, kept as an emergency
fallback for environments without PySide6. It is not maintained; new work
belongs in gui.py. Launch with `python -m shred2chart.gui_tk_legacy`.
"""
from __future__ import annotations

import json
import math
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

from . import integration, media
from .cli import ConvertError, convert_song, get_source_measure_info, peek_metadata
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


class PillButton(tk.Canvas):
    """A focusable rounded button that keeps the native Tk dependency footprint."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command=None,
        variant: str = "quiet",
        background: str,
        height: int | None = None,
        **kwargs,
    ) -> None:
        initial_state = kwargs.pop("state", "normal")
        requested_width = kwargs.pop("width", max(94, len(text) * 8 + 34))
        self._text = text
        self._command = command
        self._variant = variant
        self._surface = background
        self._enabled = initial_state != "disabled"
        self._hover = False
        self._height = height or (46 if variant == "primary" else 34)
        super().__init__(
            parent, background=background, highlightthickness=0, bd=0,
            width=requested_width, height=self._height, takefocus=True, cursor="hand2", **kwargs,
        )
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())
        self._draw()

    def _palette(self) -> tuple[str, str, str, str]:
        if not self._enabled:
            return "#18223a", "#1b2943", "#263653", "#70809e"
        if self._variant == "primary":
            return ("#13d8f4", "#1577f2", "#78ecff", "#06121f") if self._hover else ("#05cfe8", "#116eee", "#52e8ff", "#04111f")
        return ("#263f68", "#172a49", "#496d9f", "#d7e4ff") if self._hover else ("#1e3151", "#13233d", "#385885", "#d7e4ff")

    @staticmethod
    def _blend(start: str, end: str, fraction: float) -> str:
        first = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
        last = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
        return "#" + "".join(f"{round(a + (b - a) * fraction):02x}" for a, b in zip(first, last))

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), self.winfo_reqwidth())
        height = max(self.winfo_height(), self._height)
        start, end, outline, foreground = self._palette()
        radius = min(height // 2 - 2, 22)
        center_y = height / 2
        for x in range(1, width, 2):
            if x < radius:
                delta = math.sqrt(max(0, radius * radius - (x - radius) ** 2))
            elif x > width - radius:
                delta = math.sqrt(max(0, radius * radius - (x - (width - radius)) ** 2))
            else:
                delta = radius
            self.create_line(
                x, center_y - delta, x, center_y + delta,
                fill=self._blend(start, end, x / max(width - 1, 1)), width=2,
            )
        self.create_polygon(
            radius, 1, width - radius, 1, width - 1, radius, width - 1, height - radius,
            width - radius, height - 1, radius, height - 1, 1, height - radius, 1, radius,
            smooth=True, fill="", outline=outline, width=1,
        )
        if self.focus_displayof() == self:
            self.create_oval(4, 4, width - 4, height - 4, outline="#d1c8ff", width=1)
        self.create_text(
            width // 2, height // 2, text=self._text, fill=foreground,
            font=("Segoe UI", 9 if self._variant == "quiet" else 10, "bold"),
        )

    def _on_enter(self, _event) -> None:
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._draw()

    def _invoke(self, _event=None) -> str | None:
        if self._enabled and self._command is not None:
            self._command()
            return "break"
        return None

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "state" in kwargs:
            self._enabled = kwargs.pop("state") != "disabled"
            super().configure(cursor="hand2" if self._enabled else "arrow")
        result = super().configure(**kwargs) if kwargs else None
        self._draw()
        return result

    config = configure


class GlassCard(tk.Canvas):
    """A rounded, subtly graded panel with a normal Tk frame for its content."""

    def __init__(self, parent: tk.Misc, *, accent: str | None = None, **kwargs) -> None:
        self._accent = accent
        super().__init__(
            parent, background="#080c16", highlightthickness=0, bd=0,
            height=kwargs.pop("height", 80), **kwargs,
        )
        self.content = ttk.Frame(self, style="Card.TFrame", padding=(8, 10, 8, 12))
        self._window = self.create_window(1, 1, anchor="nw", window=self.content)
        self.bind("<Configure>", self._resize)

    def _resize(self, event) -> None:
        width, height = event.width, event.height
        self.delete("surface")
        radius = 17
        start, end = "#111923", "#0d131e"
        for y in range(1, height, 2):
            self.create_line(
                radius, y, width - radius, y,
                fill=PillButton._blend(start, end, y / max(height - 1, 1)),
                width=2, tags="surface",
            )
        self.create_polygon(
            radius, 1, width - radius, 1, width - 1, radius, width - 1, height - radius,
            width - radius, height - 1, radius, height - 1, 1, height - radius, 1, radius,
            smooth=True, fill="", outline="#253142", width=1, tags="surface",
        )
        if self._accent:
            self.create_arc(1, 1, radius * 2 + 1, radius * 2 + 1, start=90, extent=90, style="arc", outline=self._accent, width=3, tags="surface")
            self.create_line(1, radius, 1, height - radius, fill=self._accent, width=3, tags="surface")
            self.create_arc(1, height - radius * 2 - 1, radius * 2 + 1, height - 1, start=180, extent=90, style="arc", outline=self._accent, width=3, tags="surface")
        self.coords(self._window, 2, 2)
        self.itemconfigure(self._window, width=max(1, width - 4), height=max(1, height - 4))
        self.tag_lower("surface")


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
        self.geometry("920x900")
        self.minsize(720, 640)

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
        self.measure_tracks = tk.StringVar()
        self.offset_ms = tk.StringVar(value="0")
        self.star_power = tk.BooleanVar(value=True)
        discovered_moonscraper = find_moonscraper(self._config.get("moonscraper_exe"))
        self.moonscraper_exe = tk.StringVar(
            value=str(discovered_moonscraper) if discovered_moonscraper else ""
        )
        self.open_after_import = tk.BooleanVar(
            value=bool(self._config.get("open_in_moonscraper", True))
        )

        self.gp_file.trace_add("write", lambda *_: self._update_preview())
        self.out_dir_var.trace_add("write", lambda *_: self._update_preview())

        self._configure_theme()
        self._build_widgets()
        if not _DND_AVAILABLE:
            self._log("(tip: install 'tkinterdnd2' to enable drag-and-drop file support)")
        self.after(100, self._poll_log_queue)

    def _configure_theme(self) -> None:
        """Create a layered, glass-inspired native-Tk workspace."""
        self.configure(bg="#080c16")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#080c16")
        style.configure("Card.TFrame", background="#111a2d")
        style.configure("TLabel", background="#080c16", foreground="#d9e4fa", font=("Segoe UI", 9))
        style.configure("Card.TLabel", background="#111a2d", foreground="#d9e4fa", font=("Segoe UI", 9))
        style.configure("Muted.Card.TLabel", background="#111a2d", foreground="#8ea2c5", font=("Segoe UI", 9))
        style.configure("Section.TLabel", foreground="#84a7ff", font=("Segoe UI", 8, "bold"))
        style.configure("Card.Section.TLabel", background="#111a2d", foreground="#8faaff", font=("Segoe UI", 8, "bold"))
        style.configure("TLabelframe", background="#121a2d", foreground="#92b1ff", bordercolor="#2a3b61", relief="flat")
        style.configure("TLabelframe.Label", background="#121a2d", foreground="#92b1ff", font=("Segoe UI", 8, "bold"))
        style.configure("TEntry", fieldbackground="#0b1120", foreground="#f3f7ff", bordercolor="#334b78", padding=7)
        style.configure("TButton", background="#1d2b47", foreground="#eaf0ff", bordercolor="#3a5689", padding=(11, 7), font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#2d4270"), ("disabled", "#151c2c")])
        style.configure("Accent.TButton", background="#7459ff", foreground="#ffffff", bordercolor="#9b8aff", padding=(13, 9), font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#8a73ff")])
        style.configure("TCheckbutton", background="#121a2d", foreground="#dce7ff")
        style.map("TCheckbutton", background=[("active", "#121a2d")])
        style.configure("Card.TCheckbutton", background="#111a2d", foreground="#dce7ff")
        style.map("Card.TCheckbutton", background=[("active", "#111a2d")])
        style.configure("Quiet.TButton", background="#111a2d", foreground="#aebee0", bordercolor="#263b60", padding=(10, 7), font=("Segoe UI", 8, "bold"))
        style.map("Quiet.TButton", background=[("active", "#1a2947")])
        style.configure("Horizontal.TProgressbar", background="#63e6be", troughcolor="#0b1120", bordercolor="#263a60")

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 7}

        hero_card = GlassCard(self, height=142, accent="#00d6e8")
        hero_card.pack(fill="x", padx=18, pady=(18, 0))
        hero = hero_card.content
        mark = tk.Label(
            hero, text="T2C", background="#08cce7", foreground="#07111d",
            font=("Segoe UI", 18, "bold"), width=5, height=2,
        )
        mark.pack(side="left", padx=(22, 16), pady=28)
        copy = tk.Frame(hero, background="#111a2d")
        copy.pack(side="left", fill="both", expand=True, pady=22)
        tk.Label(
            copy, text="IMPORT WORKSPACE", background="#27385e", foreground="#b9ccff",
            font=("Segoe UI", 8, "bold"), padx=8, pady=3, anchor="w",
        ).pack(anchor="w")
        tk.Label(
            copy, text="TAB → CHART", background="#111a2d", foreground="#f7f9ff",
            font=("Segoe UI", 20, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            copy, text="Guitar Pro in.  Playable Clone Hero chart out.",
            background="#111a2d", foreground="#aabbe0", font=("Segoe UI", 10), anchor="w",
        ).pack(fill="x", pady=(2, 0))
        tk.Label(
            hero, text="01  SOURCE     02  MAP     03  PLAY",
            background="#162c4c", foreground="#b8cbff", font=("Segoe UI", 8, "bold"), padx=12, pady=8,
        ).pack(side="right", padx=22)

        main = ttk.Frame(self, padding=(18, 14, 18, 16))
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        source_card = GlassCard(main, height=190)
        source_card.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        source = source_card.content
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="SOURCE MATERIAL", style="Card.Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 5))
        ttk.Label(source, text="Guitar Pro tab", style="Card.TLabel").grid(row=1, column=0, sticky="w", **pad)
        gp_entry = ttk.Entry(source, textvariable=self.gp_file)
        gp_entry.grid(row=1, column=1, sticky="ew", **pad)
        PillButton(source, text="SELECT", command=self._pick_gp_file, background="#111a2d").grid(row=1, column=2, **pad)
        self._register_drop(gp_entry, self.gp_file, kind="gp")

        ttk.Label(source, text="Song audio", style="Card.TLabel").grid(row=2, column=0, sticky="w", **pad)
        audio_entry = ttk.Entry(source, textvariable=self.audio_file)
        audio_entry.grid(row=2, column=1, sticky="ew", **pad)
        PillButton(source, text="SELECT", command=self._pick_audio_file, background="#111a2d").grid(row=2, column=2, **pad)
        self._register_drop(audio_entry, self.audio_file, kind="audio")

        ttk.Label(source, text="Clone Hero folder", style="Card.TLabel").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(source, textvariable=self.out_dir_var).grid(row=3, column=1, sticky="ew", **pad)
        PillButton(source, text="SELECT", command=self._pick_out_dir, background="#111a2d").grid(row=3, column=2, **pad)

        summary_card = GlassCard(main, height=100, accent="#00d6e8")
        summary_card.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        summary = summary_card.content
        ttk.Label(summary, text="READY TO BUILD", style="Card.Section.TLabel").pack(anchor="w")
        ttk.Label(summary, textvariable=self.metadata_var, style="Card.TLabel", font=("Segoe UI", 13, "bold")).pack(
            anchor="w", pady=(6, 2)
        )
        ttk.Label(summary, textvariable=self.out_preview_var, style="Muted.Card.TLabel", wraplength=790).pack(
            anchor="w", pady=(0, 1)
        )

        self.convert_btn = PillButton(
            main, text="GENERATE CLONE HERO CHART  →", command=self._on_convert,
            variant="primary", background="#080c16",
        )
        self.convert_btn.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 10))

        advanced_card = GlassCard(main, height=400)
        advanced_card.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        advanced = advanced_card.content
        advanced.columnconfigure(1, weight=1)
        ttk.Label(advanced, text="CHART CONTROLS", style="Card.Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 5))

        PillButton(
            advanced, text="RELOAD A PREVIOUS IMPORT…", command=self._reload_generated_import,
            background="#111a2d",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Label(advanced, text="Album art", style="Card.TLabel").grid(row=2, column=0, sticky="w", **pad)
        art_entry = ttk.Entry(advanced, textvariable=self.album_art_file)
        art_entry.grid(row=2, column=1, sticky="ew", **pad)
        PillButton(advanced, text="SELECT", command=self._pick_album_art, background="#111a2d").grid(row=2, column=2, **pad)
        self._register_drop(art_entry, self.album_art_file, kind="image")

        ttk.Label(advanced, text="Single track", style="Card.TLabel").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.track, width=8).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Tracks to blend", style="Card.TLabel").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.tracks, width=12).grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="Track by source measure", style="Card.TLabel").grid(row=5, column=0, sticky="w", **pad)
        PillButton(
            advanced, text="CONFIGURE…", command=self._edit_measure_tracks,
            background="#111a2d", width=140,
        ).grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(advanced, textvariable=self.measure_tracks, style="Muted.Card.TLabel").grid(
            row=5, column=2, sticky="w", **pad
        )

        ttk.Checkbutton(
            advanced, text="Generate automatic Star Power", style="Card.TCheckbutton", variable=self.star_power,
        ).grid(row=6, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(advanced, text="Audio offset (ms)", style="Card.TLabel").grid(row=7, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.offset_ms, width=8).grid(row=7, column=1, sticky="w", **pad)

        ttk.Label(advanced, text="MoonScraper app", style="Card.TLabel").grid(row=8, column=0, sticky="w", **pad)
        ttk.Entry(advanced, textvariable=self.moonscraper_exe).grid(
            row=8, column=1, sticky="ew", **pad
        )
        PillButton(
            advanced, text="SELECT", command=self._pick_moonscraper, background="#111a2d",
        ).grid(row=8, column=2, **pad)
        ttk.Checkbutton(
            advanced,
            text="Open the generated chart in MoonScraper after import",
            style="Card.TCheckbutton",
            variable=self.open_after_import,
            command=self._save_moonscraper_preferences,
        ).grid(row=9, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(main, text="ACTIVITY", style="Section.TLabel").grid(row=8, column=0, sticky="w", pady=(2, 4))
        log_card = GlassCard(main, height=130, accent="#00d6e8")
        log_card.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(0, 10))
        log_frame = log_card.content
        main.rowconfigure(9, weight=1)

        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(
            log_frame, height=12, wrap="word", state="disabled",
            yscrollcommand=scrollbar.set, background="#0a1020", foreground="#c9d8f5",
            insertbackground="#ffffff", relief="flat", highlightthickness=1,
            highlightbackground="#263b60",
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        action_frame = ttk.Frame(main)
        action_frame.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        action_frame.columnconfigure((0, 1), weight=1)
        self.open_folder_btn = PillButton(
            action_frame, text="OPEN OUTPUT FOLDER", command=self._open_output_folder,
            background="#080c16", state="disabled",
        )
        self.open_folder_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.open_moonscraper_btn = PillButton(
            action_frame, text="OPEN IN MOONSCRAPER", command=self._open_in_moonscraper,
            background="#080c16", state="disabled",
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

    def _reload_generated_import(self) -> None:
        """Restore a prior generated song's source and Advanced choices."""
        selected = filedialog.askdirectory(
            title="Choose a generated Tabs2Chart song folder",
            initialdir=getattr(self, "_last_out_dir", None),
        )
        if not selected:
            return
        try:
            settings = integration.read_import_settings(selected)
        except ValueError as exc:
            messagebox.showerror("Reload generated import", str(exc))
            return
        inputs = settings["inputs"]
        advanced = settings.get("advanced", {})
        self.gp_file.set(inputs.get("gp_file") or "")
        self.audio_file.set(inputs.get("audio") or "")
        self.album_art_file.set(inputs.get("album_art") or "")
        self.out_dir_var.set(inputs.get("output_root") or str(Path(selected).parent))
        self.track.set("" if advanced.get("track") is None else str(advanced["track"]))
        self.tracks.set(advanced.get("tracks") or "")
        self.measure_tracks.set(advanced.get("measure_tracks") or "")
        self.offset_ms.set(str(advanced.get("offset_ms", 0)))
        self.star_power.set(bool(advanced.get("star_power", True)))
        self._out_dir = Path(selected)
        self.open_folder_btn.configure(state="normal")
        self.open_moonscraper_btn.configure(state="normal")
        self._update_preview()
        self._log(f"Reloaded source and Advanced settings from {selected}")

    def _edit_measure_tracks(self) -> None:
        """Open source-measure track overrides for the selected tab."""
        gp_file = self.gp_file.get().strip()
        if not gp_file or not Path(gp_file).is_file():
            messagebox.showerror("Per-measure tracks", "Choose a Guitar Pro tab first.")
            return
        try:
            tracks, measure_count = get_source_measure_info(gp_file)
        except Exception as exc:
            messagebox.showerror("Per-measure tracks", f"Could not read the tab tracks:\n{exc}")
            return

        current: dict[int, int] = {}
        for pair in self.measure_tracks.get().split(","):
            if not pair.strip():
                continue
            try:
                measure, track = pair.split(":", 1)
                current[int(measure)] = int(track)
            except ValueError:
                continue

        dialog = tk.Toplevel(self)
        dialog.title("Choose tracks by source measure")
        dialog.geometry("440x560")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=("Source measures match the Guitar Pro tab. They do not include "
                  "the generated empty lead-in bars. Auto keeps normal blending."),
            wraplength=400,
        ).pack(anchor="w", padx=12, pady=(12, 6))

        labels = {"Auto": None}
        for track_id, name in tracks:
            labels[f"{track_id}: {name}"] = track_id
        options = list(labels)

        bulk = ttk.LabelFrame(dialog, text="Apply one choice to a measure range")
        bulk.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(bulk, text="From").grid(row=0, column=0, padx=(8, 2), pady=6)
        range_start = tk.StringVar(value="1")
        ttk.Entry(bulk, textvariable=range_start, width=6).grid(row=0, column=1, pady=6)
        ttk.Label(bulk, text="through").grid(row=0, column=2, padx=(8, 2), pady=6)
        range_end = tk.StringVar(value=str(measure_count))
        ttk.Entry(bulk, textvariable=range_end, width=6).grid(row=0, column=3, pady=6)
        bulk_choice = tk.StringVar(value="Auto")
        ttk.Combobox(bulk, textvariable=bulk_choice, values=options, state="readonly", width=18).grid(
            row=0, column=4, padx=(8, 4), pady=6,
        )

        canvas = tk.Canvas(dialog, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        rows = ttk.Frame(canvas)
        rows.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=rows, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=6)
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=6)

        selections: dict[int, tk.StringVar] = {}
        for measure in range(1, measure_count + 1):
            ttk.Label(rows, text=f"Measure {measure}", width=14).grid(
                row=measure - 1, column=0, sticky="w", padx=(4, 8), pady=2,
            )
            selected = next(
                (label for label, track_id in labels.items() if track_id == current.get(measure)),
                "Auto",
            )
            variable = tk.StringVar(value=selected)
            ttk.Combobox(rows, textvariable=variable, values=options, state="readonly", width=36).grid(
                row=measure - 1, column=1, sticky="ew", padx=4, pady=2,
            )
            selections[measure] = variable

        def apply_range() -> None:
            try:
                first = int(range_start.get())
                last = int(range_end.get())
            except ValueError:
                messagebox.showerror("Per-measure tracks", "Range endpoints must be whole measure numbers.", parent=dialog)
                return
            if first < 1 or last < first or last > measure_count:
                messagebox.showerror(
                    "Per-measure tracks",
                    f"Choose a range from 1 through {measure_count}.",
                    parent=dialog,
                )
                return
            for measure in range(first, last + 1):
                selections[measure].set(bulk_choice.get())

        ttk.Button(bulk, text="Apply range", command=apply_range).grid(
            row=0, column=5, padx=(4, 8), pady=6,
        )

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=12, pady=(6, 12))

        def clear_all() -> None:
            for variable in selections.values():
                variable.set("Auto")

        def save() -> None:
            overrides = [
                f"{measure}:{labels[variable.get()]}"
                for measure, variable in selections.items()
                if labels[variable.get()] is not None
            ]
            self.measure_tracks.set(",".join(overrides) if overrides else "")
            dialog.destroy()

        ttk.Button(buttons, text="Clear all", command=clear_all).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Use selections", command=save).pack(side="right", padx=(0, 6))

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
        if track is not None and self.measure_tracks.get().strip():
            messagebox.showerror(
                "Tabs2Chart",
                "Track (verbatim) cannot be combined with per-measure track choices.",
            )
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
            measure_tracks=self.measure_tracks.get().strip() or None,
            star_power=self.star_power.get(),
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
        self.convert_btn.configure(state="normal", text="GENERATE CLONE HERO CHART  →")
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
