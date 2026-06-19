"""CLI entry point for the PyInstaller build.

Lives at the project root (outside src/furrifier_fo4/) because PyInstaller runs
it as a plain script, not as `python -m`, so the package's relative imports only
resolve when the package is imported by name — which this launcher does.
"""

from furrifier_fo4.main import main

if __name__ == "__main__":
    raise SystemExit(main())
