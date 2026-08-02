"""Desktop importer for turning a Guitar Pro tab + recording into a
ready-to-scan Clone Hero song folder.

Qt (PySide6) front end.  Run unpackaged with `python -m shred2chart.gui`, or via
the installed `shred2chart-gui` console script.  For a standalone .exe, package
this module's `main()` with PyInstaller (see packaging/gui_entry.py).

The legacy Tk implementation is kept at gui_tk_legacy.py as an unmaintained
fallback; all shared pure logic lives in gui_common.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import integration, media, qt_theme
from .cli import ConvertError, convert_song, get_source_measure_info, peek_metadata
from .gui_common import (  # noqa: F401 - re-exported for tests and back-compat
    _AUDIO_FILETYPES,
    _CONFIG_PATH,
    _GP_FILETYPES,
    _GP_SUFFIXES,
    _IMAGE_FILETYPES,
    _MOONSCRAPER_FILETYPES,
    _load_config,
    _parse_dnd_path,
    _save_config,
    _song_output_dir,
    _suggest_companion_files,
    qt_filter,
)
from .moonscraper import MoonscraperLaunchError, find_moonscraper, open_chart

_ACCEPTED_SUFFIXES = {
    "gp": _GP_SUFFIXES,
    "audio": media.AUDIO_EXTENSIONS,
    "image": media.IMAGE_EXTENSIONS,
    "exe": {".exe"},
    "dir": set(),  # handled by is_dir(), not by suffix
}


# ------------------------------------------------------------------ widgets --

def _tracked_font(size: int, weight: QFont.Weight, spacing: float = 0.0) -> QFont:
    """Qt style sheets have no letter-spacing, so tracked type is set in code."""
    font = QFont()
    font.setPixelSize(size)
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def chip(text: str, kind: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Chip" + kind)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


def eyebrow(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("Eyebrow")
    label.setFont(_tracked_font(10, QFont.Weight.Bold, 1.1))
    return label


class Card(QFrame):
    """Rounded translucent panel, optionally with a cyan accent rail."""

    def __init__(
        self,
        title: str | None = None,
        *,
        accent: bool = False,
        header_extra: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        if accent:
            rail = QFrame()
            rail.setObjectName("AccentBar")
            rail.setFixedWidth(3)
            wrap = QVBoxLayout()
            wrap.setContentsMargins(5, 10, 0, 10)
            wrap.addWidget(rail)
            shell.addLayout(wrap)

        inner = QVBoxLayout()
        inner.setContentsMargins(18 if accent else 20, 16, 20, 18)
        inner.setSpacing(12)
        shell.addLayout(inner)

        if title or header_extra:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            if title:
                head.addWidget(eyebrow(title))
            head.addStretch(1)
            if header_extra is not None:
                head.addWidget(header_extra)
            inner.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        inner.addLayout(self.body)


class DropLineEdit(QLineEdit):
    """Line edit that accepts a dropped file of an expected kind."""

    pathDropped = Signal(str)

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setAcceptDrops(True)
        self.setClearButtonEnabled(True)

    def _first_local_path(self, event) -> str | None:
        data = event.mimeData()
        if not data.hasUrls():
            return None
        for url in data.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None

    def _flag(self, active: bool) -> None:
        self.setProperty("dropTarget", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _accepts(self, path: str) -> bool:
        if self._kind == "dir":
            return Path(path).is_dir()
        return Path(path).suffix.lower() in _ACCEPTED_SUFFIXES[self._kind]

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        path = self._first_local_path(event)
        if path and self._accepts(path):
            event.acceptProposedAction()
            self._flag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._flag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._flag(False)
        path = self._first_local_path(event)
        if not path:
            event.ignore()
            return
        event.acceptProposedAction()
        self.setText(path)
        self.pathDropped.emit(path)


class FileRow(QWidget):
    """Label + drop-aware path field + Browse button, on one grid row."""

    changed = Signal(str)
    picked = Signal(str)

    def __init__(
        self,
        label: str,
        *,
        kind: str,
        placeholder: str = "",
        grid: QGridLayout,
        row: int,
        directory: bool = False,
        filetypes: list[tuple[str, str]] | None = None,
        dialog_title: str = "Choose a file",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        self._filetypes = filetypes or []
        self._dialog_title = dialog_title
        self.start_dir: str | None = None

        caption = QLabel(label)
        caption.setObjectName("FieldLabel")
        caption.setMinimumWidth(104)

        self.edit = DropLineEdit(kind)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        self.edit.pathDropped.connect(self.picked.emit)

        button = QPushButton("Browse")
        button.setObjectName("Browse")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._browse)

        grid.addWidget(caption, row, 0)
        grid.addWidget(self.edit, row, 1)
        grid.addWidget(button, row, 2)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self.edit.setText(value)
        # Long paths otherwise sit scrolled to the tail, hiding the drive letter.
        self.edit.setCursorPosition(0)

    def _browse(self) -> None:
        start = self.start_dir or ""
        if self._directory:
            path = QFileDialog.getExistingDirectory(self, self._dialog_title, start)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, self._dialog_title, start, qt_filter(self._filetypes),
            )
        if path:
            path = str(Path(path))
            self.edit.setText(path)
            self.picked.emit(path)


class StatTile(QFrame):
    """Small inset panel: coloured dot, caption, value, state chip."""

    def __init__(self, caption: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Inset")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(13, 11, 13, 12)
        outer.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(7)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {colour}; font-size: 11px;")
        title = QLabel(caption)
        title.setObjectName("CardTitle")
        title.setStyleSheet(f"color: {colour};")
        self.state = chip("waiting", "")
        head.addWidget(dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.state)
        outer.addLayout(head)

        self.value = QLabel("—")
        self.value.setObjectName("Muted")
        self.value.setWordWrap(False)
        self.value.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.value)

    def set_state(self, text: str, kind: str) -> None:
        self.state.setText(text)
        self.state.setObjectName("Chip" + kind)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def set_value(self, text: str) -> None:
        metrics = self.value.fontMetrics()
        self.value.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, 300))
        self.value.setToolTip(text)


# ------------------------------------------------------------------- worker --

class ConvertWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)

    def __init__(self, kwargs: dict) -> None:
        super().__init__()
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = convert_song(on_progress=self.progress.emit, **self._kwargs)
        except ConvertError as exc:
            self.progress.emit(f"error: {exc}")
            self.finished.emit(None)
        except Exception as exc:  # surface it, don't take the window down
            self.progress.emit(f"unexpected error: {exc}")
            self.finished.emit(None)
        else:
            self.finished.emit(result.out_dir)


# ------------------------------------------------------------------- dialog --

class MeasureTracksDialog(QDialog):
    """Per-source-measure track overrides for the selected tab."""

    def __init__(
        self,
        parent: QWidget,
        tracks: list[tuple[int, str]],
        measure_count: int,
        current: dict[int, int],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tracks by source measure")
        self.resize(560, 680)

        self._labels: dict[str, int | None] = {"Auto": None}
        for track_id, name in tracks:
            self._labels[f"{track_id}: {name}"] = track_id
        options = list(self._labels)
        self._measure_count = measure_count
        self.result_value = ""

        root = QWidget(self)
        root.setObjectName("Root")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.addWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(12)

        blurb = QLabel(
            "Source measures match the Guitar Pro tab and exclude the generated "
            "empty lead-in bars. Auto keeps normal blending."
        )
        blurb.setObjectName("Body")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        bulk = Card("Apply one choice to a range")
        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(8)
        self.range_start = QLineEdit("1")
        self.range_start.setFixedWidth(64)
        self.range_end = QLineEdit(str(measure_count))
        self.range_end.setFixedWidth(64)
        self.bulk_choice = QComboBox()
        self.bulk_choice.addItems(options)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self._apply_range)
        for widget in (
            QLabel("From"), self.range_start, QLabel("through"), self.range_end,
        ):
            if isinstance(widget, QLabel):
                widget.setObjectName("FieldLabel")
            bulk_row.addWidget(widget)
        bulk_row.addWidget(self.bulk_choice, 1)
        bulk_row.addWidget(apply_button)
        bulk.body.addLayout(bulk_row)
        outer.addWidget(bulk)

        area = QScrollArea()
        area.setWidgetResizable(True)
        rows_host = QWidget()
        rows = QGridLayout(rows_host)
        rows.setContentsMargins(2, 2, 2, 2)
        rows.setColumnStretch(1, 1)
        rows.setVerticalSpacing(5)

        self._selections: dict[int, QComboBox] = {}
        for measure in range(1, measure_count + 1):
            caption = QLabel(f"Measure {measure}")
            caption.setObjectName("FieldLabel")
            combo = QComboBox()
            combo.addItems(options)
            chosen = next(
                (label for label, tid in self._labels.items() if tid == current.get(measure)),
                "Auto",
            )
            combo.setCurrentText(chosen)
            rows.addWidget(caption, measure - 1, 0)
            rows.addWidget(combo, measure - 1, 1)
            self._selections[measure] = combo
        area.setWidget(rows_host)
        outer.addWidget(area, 1)

        footer = QHBoxLayout()
        clear = QPushButton("Clear all")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._clear_all)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Use selections")
        save.clicked.connect(self._save)
        footer.addWidget(clear)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(save)
        outer.addLayout(footer)

    def _apply_range(self) -> None:
        try:
            first = int(self.range_start.text())
            last = int(self.range_end.text())
        except ValueError:
            QMessageBox.critical(self, "Per-measure tracks", "Range endpoints must be whole measure numbers.")
            return
        if first < 1 or last < first or last > self._measure_count:
            QMessageBox.critical(
                self, "Per-measure tracks", f"Choose a range from 1 through {self._measure_count}.",
            )
            return
        for measure in range(first, last + 1):
            self._selections[measure].setCurrentText(self.bulk_choice.currentText())

    def _clear_all(self) -> None:
        for combo in self._selections.values():
            combo.setCurrentText("Auto")

    def _save(self) -> None:
        overrides = [
            f"{measure}:{self._labels[combo.currentText()]}"
            for measure, combo in self._selections.items()
            if self._labels[combo.currentText()] is not None
        ]
        self.result_value = ",".join(overrides)
        self.accept()


# ---------------------------------------------------------------- main window -

class App(QWidget):
    _log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle("Tabs2Chart Importer")
        self.resize(1140, 880)
        self.setMinimumSize(980, 720)

        self._config = _load_config()
        self._out_dir: Path | None = None
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        self._measure_tracks = ""

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._refresh_summary)

        self._build()
        self._restore_config()
        self._refresh_summary()

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        page = QVBoxLayout(self)
        page.setContentsMargins(20, 20, 20, 18)
        page.setSpacing(13)

        page.addWidget(self._header())
        page.addWidget(self._summary_card())

        columns = QHBoxLayout()
        columns.setSpacing(13)
        columns.addWidget(self._source_card(), 1)
        columns.addWidget(self._controls_card(), 1)
        page.addLayout(columns)

        self.convert_btn = QPushButton("GENERATE CLONE HERO CHART   →")
        self.convert_btn.setObjectName("Primary")
        self.convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.convert_btn.setFont(_tracked_font(14, QFont.Weight.ExtraBold, 0.8))
        self.convert_btn.clicked.connect(self._on_convert)
        page.addWidget(self.convert_btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)  # only shown while a conversion runs
        page.addWidget(self.progress)

        page.addWidget(self._activity_card(), 1)
        page.addLayout(self._footer())

        self._log_line.connect(self._log)

    def _header(self) -> QWidget:
        card = QFrame()
        card.setObjectName("HeaderCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(15)

        mark = QLabel("T2C")
        mark.setObjectName("Wordmark")
        mark.setFixedSize(50, 50)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(mark)

        copy = QVBoxLayout()
        copy.setSpacing(2)
        title = QLabel("TAB → CHART")
        title.setObjectName("Display")
        title.setFont(_tracked_font(21, QFont.Weight.Bold, 0.6))
        subtitle = QLabel("Guitar Pro in.  Playable Clone Hero chart out.")
        subtitle.setObjectName("Body")
        copy.addWidget(title)
        copy.addWidget(subtitle)
        row.addLayout(copy)
        row.addStretch(1)

        stages = chip("01  SOURCE   ·   02  MAP   ·   03  PLAY", "Cyan")
        stages.setFont(_tracked_font(11, QFont.Weight.DemiBold, 0.6))
        row.addWidget(chip("IMPORT WORKSPACE"))
        row.addWidget(stages)
        return card

    def _summary_card(self) -> QWidget:
        self.summary_chips = QWidget()
        chip_row = QHBoxLayout(self.summary_chips)
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        self.chip_tab = chip("tab", "")
        self.chip_audio = chip("audio", "")
        self.chip_art = chip("art", "")
        for widget in (self.chip_tab, self.chip_audio, self.chip_art):
            chip_row.addWidget(widget)

        card = Card("Import summary", accent=True, header_extra=self.summary_chips)

        self.song_label = QLabel("No tab selected")
        self.song_label.setObjectName("Display")
        self.song_label.setFont(_tracked_font(21, QFont.Weight.Bold, 0.2))
        card.body.addWidget(self.song_label)

        self.destination_label = QLabel("Choose a tab to preview the imported song.")
        self.destination_label.setObjectName("Mono")
        self.destination_label.setWordWrap(True)
        card.body.addWidget(self.destination_label)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tile_tab = StatTile("Guitar Pro tab", qt_theme.CYAN)
        self.tile_audio = StatTile("Song audio", qt_theme.GREEN)
        tiles.addWidget(self.tile_tab)
        tiles.addWidget(self.tile_audio)
        card.body.addLayout(tiles)
        return card

    def _source_card(self) -> QWidget:
        card = Card("Source material")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(1, 1)

        self.row_tab = FileRow(
            "Guitar Pro tab", kind="gp", grid=grid, row=0,
            placeholder="Drop a .gp / .gpx / .gp3-5 file here",
            filetypes=_GP_FILETYPES, dialog_title="Choose a Guitar Pro tab",
        )
        self.row_audio = FileRow(
            "Song audio", kind="audio", grid=grid, row=1,
            placeholder="Drop the matching recording here",
            filetypes=_AUDIO_FILETYPES, dialog_title="Choose an audio file",
        )
        self.row_art = FileRow(
            "Album art", kind="image", grid=grid, row=2,
            placeholder="Optional cover image",
            filetypes=_IMAGE_FILETYPES, dialog_title="Choose album art",
        )
        self.row_songs = FileRow(
            "Clone Hero folder", kind="dir", grid=grid, row=3,
            placeholder="Your Clone Hero Songs directory",
            directory=True, dialog_title="Choose your Songs folder",
        )
        card.body.addLayout(grid)

        self.row_tab.changed.connect(lambda _t: self._preview_timer.start())
        self.row_tab.picked.connect(self._on_tab_picked)
        self.row_audio.changed.connect(lambda _t: self._preview_timer.start())
        self.row_audio.picked.connect(lambda p: self._remember("last_audio_dir", p))
        self.row_art.changed.connect(lambda _t: self._preview_timer.start())
        self.row_songs.changed.connect(lambda _t: self._preview_timer.start())
        self.row_songs.picked.connect(lambda p: self._remember("last_out_dir", p, directory=True))

        hint = QLabel("Drag files straight onto a field, or use Browse.")
        hint.setObjectName("Muted")
        card.body.addWidget(hint)
        card.body.addStretch(1)
        return card

    def _controls_card(self) -> QWidget:
        card = Card("Chart controls")
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self._mapping_tab(), "Mapping")
        tabs.addTab(self._sync_tab(), "Sync")
        tabs.addTab(self._moonscraper_tab(), "MoonScraper")
        card.body.addWidget(tabs)
        return card

    def _mapping_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(1, 1)

        single = QLabel("Single track")
        single.setObjectName("FieldLabel")
        self.track_edit = QLineEdit()
        self.track_edit.setPlaceholderText("auto")
        self.track_edit.setFixedWidth(90)
        grid.addWidget(single, 0, 0)
        grid.addWidget(self.track_edit, 0, 1, Qt.AlignmentFlag.AlignLeft)

        blend = QLabel("Tracks to blend")
        blend.setObjectName("FieldLabel")
        self.tracks_edit = QLineEdit()
        self.tracks_edit.setPlaceholderText("e.g. 1,0")
        self.tracks_edit.setFixedWidth(130)
        grid.addWidget(blend, 1, 0)
        grid.addWidget(self.tracks_edit, 1, 1, Qt.AlignmentFlag.AlignLeft)

        per_measure = QLabel("Per-measure tracks")
        per_measure.setObjectName("FieldLabel")
        configure = QPushButton("Configure…")
        configure.setObjectName("Browse")
        configure.clicked.connect(self._edit_measure_tracks)
        self.measure_summary = QLabel("Auto for every measure")
        self.measure_summary.setObjectName("Muted")
        measure_row = QHBoxLayout()
        measure_row.setSpacing(9)
        measure_row.addWidget(configure)
        measure_row.addWidget(self.measure_summary, 1)
        grid.addWidget(per_measure, 2, 0)
        grid.addLayout(measure_row, 2, 1)
        layout.addLayout(grid)

        self.star_power = QCheckBox("Generate automatic Star Power")
        self.star_power.setChecked(True)
        layout.addWidget(self.star_power)
        layout.addStretch(1)
        return page

    def _sync_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)
        caption = QLabel("Audio offset")
        caption.setObjectName("FieldLabel")
        self.offset_edit = QLineEdit("0")
        self.offset_edit.setFixedWidth(90)
        unit = QLabel("ms")
        unit.setObjectName("Muted")
        row.addWidget(caption)
        row.addWidget(self.offset_edit)
        row.addWidget(unit)
        row.addStretch(1)
        layout.addLayout(row)

        note = QLabel(
            "Positive values push the chart later against the recording. Every "
            "import already includes two empty lead-in measures."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _moonscraper_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(1, 1)
        self.row_moonscraper = FileRow(
            "Executable", kind="exe", grid=grid, row=0,
            placeholder="Moonscraper Chart Editor.exe",
            filetypes=_MOONSCRAPER_FILETYPES, dialog_title="Choose Moonscraper Chart Editor.exe",
        )
        self.row_moonscraper.picked.connect(lambda _p: self._save_moonscraper_preferences())
        layout.addLayout(grid)

        self.open_after_import = QCheckBox("Open the generated chart after import")
        self.open_after_import.toggled.connect(lambda _v: self._save_moonscraper_preferences())
        layout.addWidget(self.open_after_import)
        layout.addStretch(1)
        return page

    def _activity_card(self) -> QWidget:
        card = Card("Activity")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)
        self.log_view.setPlaceholderText("Conversion output appears here.")
        card.body.addWidget(self.log_view)
        return card

    def _footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(9)
        reload_btn = QPushButton("Reload a previous import…")
        reload_btn.setObjectName("Ghost")
        reload_btn.clicked.connect(self._reload_generated_import)
        self.open_folder_btn = QPushButton("Open output folder")
        self.open_folder_btn.setObjectName("Ghost")
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        self.open_moonscraper_btn = QPushButton("Open in MoonScraper")
        self.open_moonscraper_btn.setEnabled(False)
        self.open_moonscraper_btn.clicked.connect(lambda: self._open_in_moonscraper())
        row.addWidget(reload_btn)
        row.addStretch(1)
        row.addWidget(self.open_folder_btn)
        row.addWidget(self.open_moonscraper_btn)
        return row

    # -- config ---------------------------------------------------------------

    def _restore_config(self) -> None:
        self.row_tab.start_dir = self._config.get("last_gp_dir")
        self.row_audio.start_dir = self._config.get("last_audio_dir")
        out_dir = self._config.get("last_out_dir") or str(Path.cwd() / "songs")
        self.row_songs.start_dir = out_dir
        self.row_songs.setText(out_dir)

        discovered = find_moonscraper(self._config.get("moonscraper_exe"))
        if discovered:
            self.row_moonscraper.setText(str(discovered))
        self.open_after_import.setChecked(bool(self._config.get("open_in_moonscraper", True)))

    def _remember(self, key: str, path: str, *, directory: bool = False) -> None:
        value = path if directory else str(Path(path).parent)
        self._config[key] = value
        _save_config(self._config)

    def _save_moonscraper_preferences(self) -> None:
        self._config["moonscraper_exe"] = self.row_moonscraper.text()
        self._config["open_in_moonscraper"] = self.open_after_import.isChecked()
        _save_config(self._config)

    # -- summary --------------------------------------------------------------

    def _on_tab_picked(self, path: str) -> None:
        self._remember("last_gp_dir", path)
        audio, art = _suggest_companion_files(path)
        if audio is not None and not self.row_audio.text():
            self.row_audio.setText(str(audio))
            self._log(f"Matched song audio: {audio.name}")
        if art is not None and not self.row_art.text():
            self.row_art.setText(str(art))
            self._log(f"Matched album art: {art.name}")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        tab = self.row_tab.text()
        audio = self.row_audio.text()
        art = self.row_art.text()

        tab_ok = bool(tab) and Path(tab).is_file()
        audio_ok = bool(audio) and Path(audio).is_file()
        art_ok = bool(art) and Path(art).is_file()

        self._set_chip(self.chip_tab, "tab", tab_ok, bool(tab))
        self._set_chip(self.chip_audio, "audio", audio_ok, bool(audio))
        self._set_chip(self.chip_art, "art", art_ok, bool(art), optional=True)

        self.tile_tab.set_value(Path(tab).name if tab else "no tab selected")
        self.tile_tab.set_state(*self._tile_state(tab, tab_ok))
        self.tile_audio.set_value(Path(audio).name if audio else "chart only, no playback")
        self.tile_audio.set_state(*self._tile_state(audio, audio_ok, optional=True))

        if not tab_ok:
            self.song_label.setText("No tab selected")
            self.destination_label.setText("Choose a tab to preview the imported song.")
            self.convert_btn.setEnabled(not self._busy())
            return

        try:
            artist, title = peek_metadata(tab)
        except Exception as exc:
            self.song_label.setText("Could not read this tab")
            self.destination_label.setText(str(exc))
            return

        self.song_label.setText(f"{artist} — {title}")
        root = self.row_songs.text() or str(Path.cwd() / "songs")
        self.destination_label.setText(str(_song_output_dir(root, artist, title)))

    @staticmethod
    def _set_chip(widget: QLabel, name: str, ok: bool, filled: bool, *, optional: bool = False) -> None:
        # Green when resolved, amber only when a path was given but is bad,
        # neutral when simply not filled in yet.
        kind = "Green" if ok else "Amber" if filled else ""
        widget.setText(f"{name}?" if filled and not ok else name)
        widget.setObjectName("Chip" + kind)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    @staticmethod
    def _tile_state(value: str, ok: bool, *, optional: bool = False) -> tuple[str, str]:
        if ok:
            return "ready", "Green"
        if value:
            return "not found", "Amber"
        return ("optional", "") if optional else ("required", "")

    def _busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    # -- log ------------------------------------------------------------------

    def _log(self, line: str) -> None:
        self.log_view.appendPlainText(line)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    # -- actions --------------------------------------------------------------

    def _edit_measure_tracks(self) -> None:
        tab = self.row_tab.text()
        if not tab or not Path(tab).is_file():
            QMessageBox.critical(self, "Per-measure tracks", "Choose a Guitar Pro tab first.")
            return
        try:
            tracks, measure_count = get_source_measure_info(tab)
        except Exception as exc:
            QMessageBox.critical(self, "Per-measure tracks", f"Could not read the tab tracks:\n{exc}")
            return

        current: dict[int, int] = {}
        for pair in self._measure_tracks.split(","):
            if not pair.strip():
                continue
            try:
                measure, track = pair.split(":", 1)
                current[int(measure)] = int(track)
            except ValueError:
                continue

        dialog = MeasureTracksDialog(self, tracks, measure_count, current)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._measure_tracks = dialog.result_value
            count = len([p for p in self._measure_tracks.split(",") if p])
            self.measure_summary.setText(
                f"{count} measure override{'s' if count != 1 else ''}" if count
                else "Auto for every measure"
            )

    def _reload_generated_import(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose a generated Tabs2Chart song folder", self.row_songs.text(),
        )
        if not selected:
            return
        try:
            settings = integration.read_import_settings(selected)
        except ValueError as exc:
            QMessageBox.critical(self, "Reload generated import", str(exc))
            return
        inputs = settings["inputs"]
        advanced = settings.get("advanced", {})
        self.row_tab.setText(inputs.get("gp_file") or "")
        self.row_audio.setText(inputs.get("audio") or "")
        self.row_art.setText(inputs.get("album_art") or "")
        self.row_songs.setText(inputs.get("output_root") or str(Path(selected).parent))
        self.track_edit.setText("" if advanced.get("track") is None else str(advanced["track"]))
        self.tracks_edit.setText(advanced.get("tracks") or "")
        self._measure_tracks = advanced.get("measure_tracks") or ""
        self.offset_edit.setText(str(advanced.get("offset_ms", 0)))
        self.star_power.setChecked(bool(advanced.get("star_power", True)))
        self._out_dir = Path(selected)
        self.open_folder_btn.setEnabled(True)
        self.open_moonscraper_btn.setEnabled(True)
        self._refresh_summary()
        self._log(f"Reloaded source and chart settings from {selected}")

    def _open_output_folder(self) -> None:
        if self._out_dir and self._out_dir.exists():
            if hasattr(os, "startfile"):
                os.startfile(self._out_dir)  # noqa: S606 - Windows, user-chosen local path
            else:
                from PySide6.QtCore import QUrl  # noqa: PLC0415
                from PySide6.QtGui import QDesktopServices  # noqa: PLC0415

                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._out_dir)))

    def _open_in_moonscraper(self, *, prompt_if_missing: bool = True) -> bool:
        if self._out_dir is None:
            return False
        executable = find_moonscraper(self.row_moonscraper.text())
        if executable is None and prompt_if_missing:
            picked, _ = QFileDialog.getOpenFileName(
                self, "Choose Moonscraper Chart Editor.exe", "",
                qt_filter(_MOONSCRAPER_FILETYPES),
            )
            if picked:
                self.row_moonscraper.setText(picked)
            executable = find_moonscraper(picked or None)
        if executable is None:
            self._log("MoonScraper was not opened: set its executable on the MoonScraper tab.")
            return False

        self.row_moonscraper.setText(str(executable))
        self._save_moonscraper_preferences()
        try:
            open_chart(
                self._out_dir / "notes.chart",
                executable,
                manifest_path=self._out_dir / "moon-scraper-manifest.json",
            )
        except MoonscraperLaunchError as exc:
            self._log(f"MoonScraper was not opened: {exc}")
            QMessageBox.warning(self, "MoonScraper", str(exc))
            return False
        self._log(f"Opened notes.chart in MoonScraper: {executable}")
        return True

    # -- conversion -----------------------------------------------------------

    def _on_convert(self) -> None:
        if self._busy():
            return
        tab = self.row_tab.text()
        if not tab:
            QMessageBox.critical(self, "Tabs2Chart", "Choose a Guitar Pro tab first.")
            return
        if Path(tab).suffix.lower() not in _GP_SUFFIXES:
            QMessageBox.critical(self, "Tabs2Chart", "Choose a .gp, .gpx, .gp3, .gp4, or .gp5 tab.")
            return

        audio = self.row_audio.text()
        if not audio:
            proceed = QMessageBox.question(
                self, "Import without audio?",
                "No song audio is selected. The chart will be created, but Clone Hero cannot "
                "play it until you add song.ogg.\n\nContinue anyway?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return
        elif not Path(audio).is_file():
            QMessageBox.critical(self, "Tabs2Chart", f"Song audio does not exist:\n{audio}")
            return

        try:
            offset_ms = int(self.offset_edit.text().strip() or "0")
            track = int(self.track_edit.text().strip()) if self.track_edit.text().strip() else None
        except ValueError:
            QMessageBox.critical(self, "Tabs2Chart", "Track and audio offset must be whole numbers.")
            return
        if track is not None and self._measure_tracks.strip():
            QMessageBox.critical(
                self, "Tabs2Chart",
                "A single verbatim track cannot be combined with per-measure track choices.",
            )
            return

        try:
            artist, title = peek_metadata(tab)
        except Exception as exc:
            QMessageBox.critical(self, "Tabs2Chart", f"Could not read the tab:\n{exc}")
            return

        root = self.row_songs.text() or str(Path.cwd() / "songs")
        out_dir = _song_output_dir(root, artist, title)
        if out_dir.exists() and any(out_dir.iterdir()):
            proceed = QMessageBox.question(
                self, "Replace existing import?",
                f"This song folder already contains files:\n{out_dir}\n\n"
                "Replace the generated chart files and keep any other files?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        kwargs = dict(
            gp_file=tab,
            out=out_dir,
            audio=audio or None,
            album_art=self.row_art.text() or None,
            track=track,
            tracks=self.tracks_edit.text().strip() or None,
            measure_tracks=self._measure_tracks.strip() or None,
            star_power=self.star_power.isChecked(),
            offset_ms=offset_ms,
        )

        self.log_view.clear()
        self.convert_btn.setEnabled(False)
        self.convert_btn.setText("IMPORTING…")
        self.open_folder_btn.setEnabled(False)
        self.open_moonscraper_btn.setEnabled(False)
        self.progress.setVisible(True)
        self._out_dir = None

        self._thread = QThread(self)
        self._worker = ConvertWorker(kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._log_line.emit)
        self._worker.finished.connect(self._on_convert_done)
        self._thread.start()

    def _on_convert_done(self, out_dir: object) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None

        self.progress.setVisible(False)
        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("GENERATE CLONE HERO CHART   →")

        if out_dir is None:
            QMessageBox.critical(
                self, "Import failed",
                "The import did not finish. See the activity log for the exact error.",
            )
            return

        self._out_dir = Path(out_dir)
        self.open_folder_btn.setEnabled(True)
        self.open_moonscraper_btn.setEnabled(True)
        opened = self._open_in_moonscraper() if self.open_after_import.isChecked() else False
        QMessageBox.information(
            self, "Import complete",
            f"Clone Hero song created successfully:\n\n{self._out_dir}\n\n"
            + (
                "The generated chart is now open in MoonScraper."
                if opened
                else "Use Open in MoonScraper to review it, then scan songs in Clone Hero."
            ),
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        super().closeEvent(event)


def main() -> int:
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Tabs2Chart")
    qt_theme.apply(app)
    window = App()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
