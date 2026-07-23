"""PyInstaller entry point: importing gui.py directly (as __main__) breaks
its relative imports, so PyInstaller must target this tiny wrapper instead."""
from shred2chart.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
