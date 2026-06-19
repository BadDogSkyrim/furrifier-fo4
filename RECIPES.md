# furrifier_fo4 — command recipes

Quick reference for running, testing, and building the FO4 furrifier, so the
commands don't have to be rederived each session.

> **Working directory matters.** The package lives under `src/` and is *not*
> pip-installed (only `esplib` is editable-installed). So the run/CLI commands
> below are run **from `furrifier_fo4/src/`** (which puts the package on the
> import path automatically). Test/build commands run **from `furrifier_fo4/`**.

## Launch the GUI

From `furrifier_fo4/src/`:

```powershell
python -m furrifier_fo4.gui
```

## Run the CLI

From `furrifier_fo4/src/`:

```powershell
python -m furrifier_fo4 [args]
python -m furrifier_fo4 --help     # full flag list
```

Key flags: `--scheme {ffo_scheme,ffo_test,single_race,test_facegen,user}`,
`--npcs EDID[,EDID...]` (restrict to specific NPCs by EditorID),
`--faction EDID[,EDID...]`, `--resources DIR` (override resource dir, searched
before the game Data folder), `-o/--output DIR`, `--no-facegen`, `--limit N`.

### Targeted facegen-parity test run (John + Rosalind)

```powershell
python -m furrifier_fo4 --scheme test_facegen --npcs John,RosalindOrman -o C:\tmp\ffo_test_out
```

## Tests

From `furrifier_fo4/`:

```powershell
python -m pytest                                  # full suite
python -m pytest tests/test_facegen_parity.py     # golden CK-parity test
python -m pytest -m gamefiles                      # only tests needing real FO4 game files
```

## PyInstaller build

From `furrifier_fo4/`:

```powershell
pyinstaller furrify_fo4.spec --noconfirm --clean
```

For races/schemes TOML iteration, sync `dist/` directly (sub-second) instead of
a full rebuild (~30s) — see the "kit rebuild shortcut" memory.
