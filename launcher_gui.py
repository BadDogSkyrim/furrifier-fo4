"""GUI entry point for the PyInstaller build. See launcher.py for why it lives
at the project root rather than inside the package."""

from furrifier_fo4.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
