"""Dark, glass-inspired Qt theme for the Tabs2Chart importer.

Everything visual lives here so the window code stays about layout and wiring.
Qt style sheets cannot reference inline SVG, so the few glyphs we need (check
mark, chevron) are written to a temp directory on first use and spliced into the
sheet by path.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

# ---------------------------------------------------------------- palette ----

BG_APP = "#07080e"          # window backdrop
BG_CARD = "#0f1219"         # raised panel
BG_CARD_HI = "#141824"      # nested panel / table header
BG_FIELD = "#0a0d14"        # text input well
BG_PILL = "#1a2030"         # inert chip

BORDER = "#232a38"
BORDER_HI = "#2f3849"
BORDER_FOCUS = "#22d3ee"

TXT = "#e8ebf4"
TXT_DIM = "#98a2b8"
TXT_MUTE = "#69738a"
TXT_ON_ACCENT = "#04121c"

CYAN = "#22d3ee"
CYAN_DEEP = "#0891b2"
BLUE = "#3b82f6"
PURPLE = "#a855f7"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"

UI_FONT_STACK = ['Segoe UI Variable Text', 'Segoe UI', 'Inter', 'Cantarell', 'DejaVu Sans']
MONO_FONT_STACK = ['Cascadia Mono', 'Consolas', 'JetBrains Mono', 'DejaVu Sans Mono']

_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
    '<path d="M2 6.4 L4.6 9 L10 3.2" fill="none" stroke="{color}" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
    '<path d="M2 3.6 L5 6.6 L8 3.6" fill="none" stroke="{color}" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_asset_dir: Path | None = None


def _assets() -> dict[str, str]:
    """Materialise the SVG glyphs and return QSS-safe (forward slash) paths."""
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = Path(tempfile.mkdtemp(prefix="tabs2chart-theme-"))
        (_asset_dir / "check.svg").write_text(_CHECK_SVG.format(color=TXT_ON_ACCENT), encoding="utf-8")
        (_asset_dir / "check-dim.svg").write_text(_CHECK_SVG.format(color=TXT_MUTE), encoding="utf-8")
        (_asset_dir / "chevron.svg").write_text(_CHEVRON_SVG.format(color=TXT_DIM), encoding="utf-8")
    return {
        "check": (_asset_dir / "check.svg").as_posix(),
        "check_dim": (_asset_dir / "check-dim.svg").as_posix(),
        "chevron": (_asset_dir / "chevron.svg").as_posix(),
    }


def stylesheet() -> str:
    a = _assets()
    return f"""
/* ------------------------------------------------------------ base ------ */
QWidget {{
    background: transparent;
    color: {TXT};
    font-size: 13px;
}}
QWidget#Root {{
    background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
                                stop:0 #0b0e17, stop:0.55 {BG_APP}, stop:1 #06070c);
}}
QToolTip {{
    background: {BG_CARD_HI};
    color: {TXT};
    border: 1px solid {BORDER_HI};
    border-radius: 6px;
    padding: 5px 8px;
}}

/* ------------------------------------------------------------ cards ----- */
QFrame#Card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#HeaderCard {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #121826, stop:0.5 {BG_CARD}, stop:1 #0d1017);
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame#Inset {{
    background: {BG_FIELD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#Divider {{
    background: {BORDER};
    max-height: 1px;
    border: none;
}}
QFrame#AccentBar {{
    border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {CYAN}, stop:1 {BLUE});
}}

/* ------------------------------------------------------------ text ------ */
QLabel#Eyebrow {{
    color: {CYAN};
    font-size: 10px;
    font-weight: 700;
}}
QLabel#CardTitle {{
    color: {TXT};
    font-size: 13px;
    font-weight: 700;
}}
QLabel#Display {{
    color: #f6f8ff;
    font-size: 21px;
    font-weight: 700;
}}
QLabel#Headline {{
    color: {TXT};
    font-size: 15px;
    font-weight: 700;
}}
QLabel#Body {{ color: {TXT_DIM}; }}
QLabel#Muted {{ color: {TXT_MUTE}; font-size: 12px; }}
QLabel#FieldLabel {{ color: {TXT_DIM}; font-size: 12px; }}
QLabel#Mono {{
    color: {TXT_DIM};
    font-family: '{MONO_FONT_STACK[0]}', '{MONO_FONT_STACK[1]}', monospace;
    font-size: 12px;
}}

/* chips -------------------------------------------------------------------*/
QLabel#Chip {{
    background: {BG_PILL};
    color: {TXT_DIM};
    border: 1px solid {BORDER_HI};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#ChipCyan {{
    background: rgba(34, 211, 238, 0.12);
    color: {CYAN};
    border: 1px solid rgba(34, 211, 238, 0.35);
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#ChipPurple {{
    background: rgba(168, 85, 247, 0.13);
    color: #d8b4fe;
    border: 1px solid rgba(168, 85, 247, 0.38);
    border-radius: 11px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#ChipGreen {{
    background: rgba(52, 211, 153, 0.13);
    color: {GREEN};
    border: 1px solid rgba(52, 211, 153, 0.35);
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#ChipAmber {{
    background: rgba(251, 191, 36, 0.12);
    color: {AMBER};
    border: 1px solid rgba(251, 191, 36, 0.32);
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#Wordmark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {CYAN}, stop:1 {BLUE});
    color: {TXT_ON_ACCENT};
    border-radius: 12px;
    font-size: 17px;
    font-weight: 800;
}}

/* ------------------------------------------------------------ inputs ---- */
QLineEdit {{
    background: {BG_FIELD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 7px 11px;
    min-height: 20px;
    selection-background-color: {CYAN_DEEP};
    selection-color: #ffffff;
}}
QLineEdit:hover {{ border-color: {BORDER_HI}; }}
QLineEdit:focus {{
    border-color: {BORDER_FOCUS};
    background: #0c1119;
}}
QLineEdit[dropTarget="true"] {{
    border: 1px dashed {CYAN};
    background: rgba(34, 211, 238, 0.07);
}}
QLineEdit:disabled {{ color: {TXT_MUTE}; background: #090b11; }}

QComboBox {{
    background: {BG_FIELD};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 6px 10px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {BORDER_HI}; }}
QComboBox:focus {{ border-color: {BORDER_FOCUS}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{a['chevron']}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {BG_CARD_HI};
    border: 1px solid {BORDER_HI};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: rgba(34, 211, 238, 0.18);
    selection-color: {TXT};
}}

QCheckBox {{ color: {TXT_DIM}; spacing: 9px; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {BORDER_HI};
    border-radius: 5px;
    background: {BG_FIELD};
}}
QCheckBox::indicator:hover {{ border-color: {CYAN}; }}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {CYAN}, stop:1 {BLUE});
    border-color: {CYAN};
    image: url("{a['check']}");
}}
QCheckBox::indicator:checked:disabled {{
    background: {BG_PILL};
    border-color: {BORDER};
    image: url("{a['check_dim']}");
}}

/* ------------------------------------------------------------ buttons --- */
QPushButton {{
    background: {BG_CARD_HI};
    color: {TXT};
    border: 1px solid {BORDER_HI};
    border-radius: 9px;
    padding: 7px 15px;
    min-height: 20px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #1b2231; border-color: #3a465c; }}
QPushButton:pressed {{ background: #11161f; }}
QPushButton:disabled {{ color: {TXT_MUTE}; background: #0c0f16; border-color: {BORDER}; }}

QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {CYAN}, stop:1 {BLUE});
    color: {TXT_ON_ACCENT};
    border: 1px solid rgba(120, 236, 255, 0.55);
    border-radius: 12px;
    padding: 13px 22px;
    font-size: 14px;
    font-weight: 800;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #48e2f7, stop:1 #5b9bff);
}}
QPushButton#Primary:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #17b6cf, stop:1 #2f6fdc);
}}
QPushButton#Primary:disabled {{
    background: #131a26;
    color: {TXT_MUTE};
    border-color: {BORDER};
}}

QPushButton#Ghost {{
    background: transparent;
    color: {TXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 7px 13px;
    min-height: 20px;
    font-size: 12px;
}}
QPushButton#Ghost:hover {{ color: {TXT}; border-color: {CYAN}; background: rgba(34,211,238,0.06); }}
QPushButton#Ghost:disabled {{ color: #4b5364; border-color: #1b212c; background: transparent; }}

QPushButton#Browse {{
    background: {BG_PILL};
    color: {TXT_DIM};
    border: 1px solid {BORDER_HI};
    border-radius: 9px;
    padding: 7px 14px;
    min-height: 20px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#Browse:hover {{ background: #212a3d; color: {TXT}; border-color: #3d4a61; }}

/* ------------------------------------------------------------ tabs ------ */
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {TXT_MUTE};
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 6px 15px;
    margin-right: 4px;
    font-size: 12px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TXT_DIM}; background: rgba(255,255,255,0.03); }}
QTabBar::tab:selected {{
    background: rgba(34, 211, 238, 0.12);
    color: {CYAN};
    border: 1px solid rgba(34, 211, 238, 0.32);
}}

/* ------------------------------------------------------------ progress -- */
QProgressBar {{
    background: {BG_FIELD};
    border: 1px solid {BORDER};
    border-radius: 5px;
    max-height: 8px;
    min-height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {CYAN}, stop:1 {BLUE});
}}

/* ------------------------------------------------------------ log ------- */
QPlainTextEdit#Log {{
    background: {BG_FIELD};
    border: 1px solid {BORDER};
    border-radius: 11px;
    padding: 9px 11px;
    color: #bcc7de;
    font-family: '{MONO_FONT_STACK[0]}', '{MONO_FONT_STACK[1]}', monospace;
    font-size: 12px;
    selection-background-color: {CYAN_DEEP};
}}

/* ------------------------------------------------------------ scroll ---- */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: #2b3346;
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #3a4459; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
QScrollBar::handle:horizontal {{ background: #2b3346; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: #3a4459; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ------------------------------------------------------------ dialogs --- */
QDialog {{ background: {BG_APP}; }}
QMessageBox {{ background: {BG_CARD}; }}
QMessageBox QLabel {{ color: {TXT}; }}
"""


def apply(app) -> None:
    """Apply the Fusion base style plus our sheet to a QApplication."""
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QStyleFactory

    app.setStyle(QStyleFactory.create("Fusion"))

    available = set(QFontDatabase.families())
    family = next((name for name in UI_FONT_STACK if name in available), None)
    font = QFont(family) if family else QFont()
    font.setPointSize(10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    app.setStyleSheet(stylesheet())
