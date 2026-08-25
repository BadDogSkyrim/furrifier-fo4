"""GUI command line and data-dir handling.

`--data-dir` exists so a Mod Organizer executable definition can launch
the GUI already pointed at the Data folder MO2 virtualizes.
Auto-detection goes through the registry to the Steam install, which for
a Wabbajack stock-game modlist is the one Data folder guaranteed NOT to
hold the mods.

Mirrors furrifier/tests/integration/test_gui.py on the Skyrim side.
"""

import sys

import pytest


@pytest.fixture(scope="session")
def qapp():
    """A singleton QApplication. Must exist before any QWidget."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestArgParsing:

    def test_data_dir_is_ours_qt_switches_are_not(self):
        """parse_known_args, not parse_args — Qt owns -style/-platform and
        swallowing them would break them."""
        from furrifier_fo4.gui import _parse_gui_args
        assert _parse_gui_args(["g.exe"]) == (None, ["g.exe"])
        assert _parse_gui_args(
            ["g.exe", "--data-dir", r"D:\ML\Stock Game\Data"]
        ) == (r"D:\ML\Stock Game\Data", ["g.exe"])
        assert _parse_gui_args(
            ["g.exe", "--data-dir", "X", "-style", "fusion"]
        ) == ("X", ["g.exe", "-style", "fusion"])

    def test_typo_in_our_switch_is_reported_not_swallowed(self):
        """`--datadir` would otherwise start with the auto-detected Steam
        folder and look entirely normal — the precise failure --data-dir
        exists to prevent."""
        from furrifier_fo4 import gui

        gui._startup_notes.clear()
        data_dir, qt_argv = gui._parse_gui_args(["g.exe", "--datadir", "X"])
        try:
            assert data_dir is None
            note = " ".join(gui._startup_notes)
            assert "--datadir" in note and "check the spelling" in note
        finally:
            gui._startup_notes.clear()

    def test_bad_switch_does_not_kill_a_windowed_build(self, monkeypatch):
        """A windowed PyInstaller build has no stdout/stderr. argparse
        writes --help and its errors there, so `--data-dir` with no value
        used to raise inside argparse and kill the GUI before it drew
        anything — a typo in an MO2 entry with no visible cause."""
        from furrifier_fo4 import gui

        gui._startup_notes.clear()
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)

        data_dir, qt_argv = gui._parse_gui_args(["g.exe", "--data-dir"])
        try:
            assert data_dir is None
            assert qt_argv == ["g.exe"]
            assert gui._startup_notes, "the reason must survive for the pane"
            assert "Command line ignored" in gui._startup_notes[0]
        finally:
            gui._startup_notes.clear()


class TestSameDir:

    def test_equivalent_spellings_are_the_same_folder(self):
        from furrifier_fo4.gui import _same_dir
        assert _same_dir(r"C:\A\B", "c:" + "\\" + "a" + "\\" + "b" + "\\")
        assert _same_dir("C:/A/B", r"C:\A\B")
        assert _same_dir("", "")

    def test_different_folders_are_different(self):
        from furrifier_fo4.gui import _same_dir
        assert not _same_dir("A", "B")
        assert not _same_dir("", r"C:\A")


class TestDataDirChange:

    def test_override_populates_the_field(self, qapp, tmp_path):
        from furrifier_fo4 import gui as gui_mod
        win = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
        try:
            assert win.data_dir.text() == str(tmp_path)
        finally:
            win.deleteLater()

    def test_changing_the_dir_clears_the_selection(self, qapp, tmp_path):
        """A selection is filenames valid in one directory. Carried into
        another it misreports what a run will actually load."""
        from furrifier_fo4 import gui as gui_mod
        win = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
        try:
            win._selected_plugins = ["Fallout4.esm", "FurryFallout.esp"]
            win.plugins_label.setText("2 plugins selected")

            other = tmp_path / "other"
            other.mkdir()
            win.data_dir.setText(str(other))
            win._on_data_dir_changed()

            assert win._selected_plugins is None
            assert win.plugins_label.text() == "enabled plugins"
        finally:
            win.deleteLater()

    def test_same_dir_keeps_the_selection(self, qapp, tmp_path):
        """Re-focusing the field, or a Browse landing on the same folder,
        is not a change — clearing there would be a nasty surprise."""
        from furrifier_fo4 import gui as gui_mod
        win = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
        try:
            win._selected_plugins = ["Fallout4.esm"]
            win.data_dir.setText(str(tmp_path) + "\\")
            win._on_data_dir_changed()
            assert win._selected_plugins == ["Fallout4.esm"]
        finally:
            win.deleteLater()

    def test_warns_when_the_load_order_is_absent(self, qapp, tmp_path,
                                                 monkeypatch, caplog):
        """The MO2 case: the enabled load order is real, the chosen
        directory holds none of it. Say so at the moment of choosing, not
        after a long load that yields an empty patch."""
        import logging
        import esplib
        from furrifier_fo4 import gui as gui_mod

        class _LO:
            plugins = ["Fallout4.esm", "FurryFallout.esp", "Other.esp"]

        monkeypatch.setattr(esplib.LoadOrder, "from_game",
                            staticmethod(lambda *a, **k: _LO()))
        (tmp_path / "Fallout4.esm").write_bytes(b"")

        win = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
        try:
            with caplog.at_level(logging.WARNING):
                win._warn_if_load_order_absent(str(tmp_path))
            assert "2 of 3 active plugins are not in" in caplog.text
            assert "Mod Organizer" in caplog.text
        finally:
            win.deleteLater()
