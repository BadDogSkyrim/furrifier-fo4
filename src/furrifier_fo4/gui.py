"""Fallout 4 Furrifier GUI (PySide6).

Phase 1 — the shell: a config form over `session.run` with a threaded run and
a live log pane. The 3D preview pane (QtQuick3D, loading the assembled
facegeom) arrives in phase 2, mirroring the Skyrim furrifier's preview/.
The worker-thread + log-bridge pattern matches the Skyrim GUI one-for-one.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QIntValidator
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from .config import FurrifierConfig, setup_logging
from .loader import list_available_schemes
from .main import run_furrification

_FACEGEN_SIZES = ("256", "512", "1024", "2048", "4096")


def _asset_path(name: str) -> Path:
    """Locate an asset file in dev mode or inside a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "furrifier_fo4" / "assets" / name  # type: ignore[attr-defined]
    return Path(__file__).parent / "assets" / name


# Black-and-green theme (FO4 / Pip-Boy flavor), mirroring the Skyrim tool's
# black-and-gold QSS structure with a green accent.
#   bg #121410 · surface #1B201B · border #2C332C · ghost border #3C463C
#   accent #4CC94C · accent text #BFF5BF · check-bg #234D23
#   text #D6E0D2 · ghost text #A8B5A3 · label #8A968A · placeholder #5E665A
_APP_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #121410;
    color: #D6E0D2;
}
QLabel {
    color: #8A968A;
    background-color: transparent;
}
QLineEdit, QComboBox, QPlainTextEdit {
    background-color: #1B201B;
    color: #D6E0D2;
    border: 1px solid #2C332C;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #234D23;
    selection-color: #BFF5BF;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #4CC94C;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #1B201B;
    color: #D6E0D2;
    border: 1px solid #2C332C;
    selection-background-color: #234D23;
    selection-color: #BFF5BF;
}
QFrame { background-color: transparent; border: none; }
QPushButton {
    background-color: transparent;
    color: #A8B5A3;
    border: 1px solid #3C463C;
    border-radius: 4px;
    padding: 4px 14px;
    min-height: 14px;
}
QPushButton:hover {
    background-color: #2F5A2F;
    color: #BFF5BF;
    border-color: #4CC94C;
}
QPushButton:pressed { background-color: #234D23; }
QPushButton:disabled { color: #5E665A; border-color: #2C332C; }
QPushButton[primary="true"] {
    background-color: #4CC94C;
    color: #121410;
    border: 1px solid #4CC94C;
}
QPushButton[primary="true"]:hover {
    background-color: #5FD35F;
    border-color: #5FD35F;
}
QPushButton[primary="true"]:pressed { background-color: #3FA83F; }
QPushButton[primary="true"]:disabled {
    background-color: transparent;
    color: #5E665A;
    border-color: #2C332C;
}
QCheckBox { color: #D6E0D2; spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; }
QCheckBox::indicator:unchecked {
    background-color: transparent;
    border: 1px solid #3C463C;
}
QCheckBox::indicator:checked {
    background-color: #234D23;
    border: 1px solid #4CC94C;
    image: url("{check_icon}");
}
QCheckBox::indicator:disabled { border-color: #2C332C; }
QListWidget, QComboBox QAbstractItemView { selection-color: #BFF5BF; }
QSplitter::handle { background-color: #2C332C; }
"""


class _LogBridge(QObject):
    """Carries log records from the worker thread to the GUI thread."""

    new_log = Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.new_log.emit(self.format(record))
        except Exception:
            pass


class _Worker(QThread):
    """Runs the furrification on a background thread."""

    finished_ok = Signal(int)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(str, int, int)   # phase label, current, total (0=busy)

    def __init__(self, config: FurrifierConfig, cache):
        super().__init__()
        self.config = config
        # The shared world cache (same instance the preview uses) — so a preview
        # then a Run pays the plugin load once.
        self._cache = cache
        # Cooperative-cancel flag the pipeline samples at phase boundaries and
        # per-NPC checkpoints. set() is thread-safe.
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:  # QThread.run override
        from .main import CancelledError
        try:
            world = self._cache.get_or_build(
                self.config.race_scheme, self.config.data_dir,
                self.config.plugins,
                progress=lambda m: self.progress.emit(m, 0, 0))
            code = run_furrification(
                self.config, world=world,
                progress=lambda phase, cur, tot: self.progress.emit(
                    phase, cur, tot),
                cancel_event=self._cancel_event)
            self.finished_ok.emit(code)
        except CancelledError:
            logging.getLogger(__name__).info("Furrification cancelled by user")
            self.cancelled.emit()
        except Exception as exc:  # pragma: no cover - GUI safety net
            logging.getLogger(__name__).exception("run failed")
            self.failed.emit(str(exc))


class FurrifierWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fallout 4 Furrifier")
        self.resize(1080, 960)  # preferred; clamped to the screen on first show
        self._fitted = False
        self._worker: Optional[_Worker] = None
        # One shared loaded world for both the preview and the Run, so plugins
        # load once. The Run reads it (writes a separate patch), so no
        # invalidation is needed; a scheme/plugins/data change rebuilds on the
        # next request.
        from .world import WorldCache
        self._world_cache = WorldCache()
        self._bridge = _LogBridge()
        self._bridge.new_log.connect(self._append_log)
        self._log_handler: Optional[_QtLogHandler] = None
        # None = use the game's enabled (active) plugins; a list = the
        # explicit selection from the plugin picker.
        self._selected_plugins: Optional[list] = None
        # Last committed Data dir, to suppress no-op reloads on focus-out.
        self._last_data_dir: str = ""
        self._build()
        # Install the log handler up front (not just during a Run) so warnings
        # emitted while the preview builds its catalog — e.g. a malformed color
        # rule in races/*.toml — surface in the log pane instead of vanishing.
        self._install_log_handler()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Clamp/center once, on first show, when the frame margins and the
        # actual screen the window landed on are known.
        if not self._fitted:
            self._fitted = True
            self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Shrink the window to the current screen's work area (if the
        preferred size is taller/wider than it) and center it, so the default
        never spills off a smaller display."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        # Reserve room for the window frame so the OUTER frame fits, not just
        # the client area.
        extra = self.frameGeometry().size() - self.geometry().size()
        w = min(self.width(), avail.width() - extra.width())
        h = min(self.height(), avail.height() - extra.height())
        if w != self.width() or h != self.height():
            self.resize(w, h)
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        # Never push the title bar off the top-left, even if the content's
        # minimum size exceeds the work area — keep it grabbable.
        frame.moveLeft(max(frame.left(), avail.left()))
        frame.moveTop(max(frame.top(), avail.top()))
        self.move(frame.topLeft())

    # ------------------------------------------------------------- widgets --

    def _build(self) -> None:
        from PySide6.QtWidgets import QSplitter
        from .preview.pane import PreviewPane
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)
        left = QWidget()
        root = QVBoxLayout(left)

        form = QFormLayout()
        self.scheme = QComboBox()
        schemes = list_available_schemes()
        # Default to the first scheme (alphabetical); the addItems call already
        # selects index 0. Don't preselect "user" — it's the empty template and
        # sorts last, so it'd open the GUI with 0 furrifiable NPCs.
        self.scheme.addItems(schemes or ["user"])
        form.addRow("Scheme", self.scheme)

        self.patch = QLineEdit("FO4FurryPatch.esp")
        form.addRow("Patch file", self.patch)

        self.data_dir = QLineEdit()
        self.data_dir.setPlaceholderText("auto-detect Fallout 4 Data")
        form.addRow("Data dir (read)", self._with_browse(self.data_dir,
                                                         self._browse_data))
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("defaults to Data dir")
        form.addRow("Output dir (write)",
                    self._with_browse(self.output_dir, self._browse_output))

        plugins_row = QHBoxLayout()
        plugins_row.setContentsMargins(0, 0, 0, 0)
        self.plugins_label = QLabel("enabled plugins")
        # Inset the text to match a QLineEdit's (border 1px + padding 8px) so
        # it lines up with the other fields' values.
        self.plugins_label.setContentsMargins(9, 0, 0, 0)
        plugins_row.addWidget(self.plugins_label, 1)
        plugins_btn = QPushButton("Plugins…")
        plugins_btn.clicked.connect(self._open_plugin_picker)
        plugins_row.addWidget(plugins_btn)
        form.addRow("Plugins", self._wrap(plugins_row))

        self.only_faction = QLineEdit()
        self.only_faction.setPlaceholderText(
            "optional: SettlementDiamondCity,SettlementGoodneighbor")
        form.addRow("Only factions", self.only_faction)

        self.limit = QLineEdit()
        self.limit.setValidator(QIntValidator(1, 100000, self))
        self.limit.setPlaceholderText("optional: cap NPC count")
        form.addRow("Limit", self.limit)

        # FaceGen row: "Build FaceGen" and "Size" share a row, ~4em apart.
        # Zero margins so the checkbox lines up at the field column's left
        # edge (matching the line edits and the Re-furrify checkbox below).
        em4 = self.fontMetrics().horizontalAdvance("m") * 4
        opts = QHBoxLayout()
        opts.setContentsMargins(0, 0, 0, 0)
        self.build_facegen = QCheckBox("Build FaceGen")
        self.build_facegen.setChecked(True)
        self.build_facegen.toggled.connect(
            lambda on: self.facegen_size.setEnabled(on))
        opts.addWidget(self.build_facegen)
        opts.addSpacing(em4)
        opts.addWidget(QLabel("Size"))
        self.facegen_size = QComboBox()
        self.facegen_size.addItems(_FACEGEN_SIZES)
        self.facegen_size.setCurrentText("1024")
        opts.addWidget(self.facegen_size)
        opts.addSpacing(em4)
        self.throttle = QCheckBox("Throttle")
        self.throttle.setToolTip(
            "Bake FaceGen with a single low-priority worker so the machine "
            "stays usable (slower). Off = all cores.")
        self.build_facegen.toggled.connect(self.throttle.setEnabled)
        opts.addWidget(self.throttle)
        opts.addStretch(1)
        form.addRow("FaceGen", self._wrap(opts))

        # Re-furrify NPCs an earlier run already furrified, vs leave them as-is.
        self.refurrify = QCheckBox("Re-furrify already-furry NPCs")
        self.refurrify.setChecked(True)
        self.refurrify.setToolTip(
            "On: NPCs a previous run furrified are re-rolled from their vanilla "
            "base.\nOff: they're left untouched — runs skip them, the preview "
            "shows their existing look.")
        form.addRow("", self.refurrify)

        root.addLayout(form)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        root.addWidget(self.log, 1)

        # Determinate during the furrify loop / facegen bake (range set from the
        # progress signal's total); busy (range 0,0) during opaque phases like
        # plugin load. Hidden when idle.
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        bottom = QHBoxLayout()
        self.status = QLabel("Ready")
        bottom.addWidget(self.status, 1)
        # One button doubles as Run / Cancel — its label tracks worker state.
        self.run_btn = QPushButton("Run")
        self.run_btn.setProperty("primary", True)
        self.run_btn.clicked.connect(self._on_run)
        bottom.addWidget(self.run_btn)
        root.addLayout(bottom)

        splitter.addWidget(left)
        self.preview = PreviewPane(self._config, self._world_cache, self)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 440])

        # Config changes propagate to the preview. Connected after the preview
        # exists so the construction-time addItems above doesn't fire it.
        # Scheme change drops the loaded session (keeps the NPC list);
        # refurrify toggle re-bakes the current head.
        self.scheme.currentTextChanged.connect(
            lambda *_: self.preview.on_scheme_changed())
        self.refurrify.toggled.connect(
            lambda *_: self.preview.on_refurrify_changed())
        # Data dir change re-loads everything (NPC set + session). editingFinished
        # also fires on plain focus-out, so _on_data_dir_changed guards on the
        # value actually changing.
        self.data_dir.editingFinished.connect(self._on_data_dir_changed)

    def closeEvent(self, event) -> None:
        try:
            self.preview.shutdown()
        except Exception:
            pass
        try:
            self._world_cache.close()
        except Exception:
            pass
        super().closeEvent(event)

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    def _with_browse(self, line: QLineEdit, slot) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(slot)
        row.addWidget(btn)
        return self._wrap(row)

    # ------------------------------------------------------------- actions --

    def _browse_data(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Fallout 4 Data directory")
        if d:
            self.data_dir.setText(d)
            self._on_data_dir_changed()

    def _on_data_dir_changed(self) -> None:
        """Reload the preview's NPC set + session when the Data dir actually
        changes (not on every focus-out)."""
        cur = self.data_dir.text().strip()
        if cur == self._last_data_dir:
            return
        self._last_data_dir = cur
        self.preview.on_load_order_changed()

    def _browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output directory")
        if d:
            self.output_dir.setText(d)

    def _resolve_data_dir(self) -> Optional[str]:
        d = self.data_dir.text().strip()
        if d:
            return d
        try:
            from esplib import find_game_data
            return str(find_game_data("fo4"))
        except Exception:
            return None

    def _open_plugin_picker(self) -> None:
        from pathlib import Path
        from .plugin_picker import PluginPickerDialog
        data_dir = self._resolve_data_dir()
        if not data_dir:
            self.status.setText("Set the Data dir first to pick plugins.")
            return
        dlg = PluginPickerDialog(self, Path(data_dir),
                                 initial_selection=self._selected_plugins,
                                 exclude=self.patch.text().strip() or None)
        if dlg.exec() and dlg.result is not None:
            changed = dlg.result != self._selected_plugins
            self._selected_plugins = dlg.result
            self.plugins_label.setText(f"{len(dlg.result)} plugins selected")
            if changed:
                self.preview.on_load_order_changed()

    def _config(self) -> FurrifierConfig:
        patch = self.patch.text().strip() or "FO4FurryPatch.esp"
        factions = [f.strip() for f in self.only_faction.text().split(",")
                    if f.strip()] or None
        limit = int(self.limit.text()) if self.limit.text().strip() else None
        return FurrifierConfig(
            patch_filename=patch,
            race_scheme=self.scheme.currentText(),
            build_facegen=self.build_facegen.isChecked(),
            facegen_size=int(self.facegen_size.currentText()),
            limit=limit,
            only_faction=factions,
            data_dir=self.data_dir.text().strip() or None,
            output_dir=self.output_dir.text().strip() or None,
            plugins=self._selected_plugins,
            refurrify_existing=self.refurrify.isChecked(),
            throttle=self.throttle.isChecked(),
        )

    def _on_run(self) -> None:
        # Running → the button is a Cancel: request a cooperative cancel and
        # wait for the worker to unwind at its next checkpoint.
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.run_btn.setEnabled(False)
            self.run_btn.setText("Cancelling…")
            self.status.setText("Cancelling…")
            return
        config = self._config()
        self.log.clear()  # fresh pane per run (the log FILE keeps accumulating)
        self._install_log_handler()
        self.run_btn.setText("Cancel")    # repurpose for the run's duration
        self.status.setText("Starting…")
        self.progress_bar.setRange(0, 0)  # busy until the first progress signal
        self.progress_bar.show()
        self._worker = _Worker(config, self._world_cache)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _on_progress(self, phase: str, current: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            self.status.setText(f"{phase}  {current}/{total}")
        else:
            self.progress_bar.setRange(0, 0)   # indeterminate / busy
            self.status.setText(phase)

    def _reset_run_button(self) -> None:
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run")
        self.progress_bar.hide()
        self._worker = None

    def _on_done(self, code: int) -> None:
        self.status.setText("Done" if code == 0 else f"Finished (exit {code})")
        self._reset_run_button()

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"Failed: {message}")
        self._reset_run_button()

    def _on_cancelled(self) -> None:
        self.status.setText("Cancelled")
        self._reset_run_button()

    # --------------------------------------------------------------- logs ---

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _install_log_handler(self) -> None:
        if self._log_handler is not None:
            return
        handler = _QtLogHandler(self._bridge)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        # Floor the handler at INFO so sub-INFO noise never reaches the pane —
        # incl. DEBUG re-emitted from facegen WORKER processes via the
        # QueueListener (respect_handler_level=True), where PyNifly's import-time
        # basicConfig(level=DEBUG) re-cranks the worker root past our main-process
        # pynifly->WARNING. The handler is the one gate that covers every process.
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("pynifly").setLevel(logging.WARNING)
        logging.getLogger("esplib").setLevel(logging.WARNING)
        self._log_handler = handler

    def _remove_log_handler(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None


def main() -> int:
    # Required before any ProcessPoolExecutor (facegen bake) in a frozen
    # (PyInstaller) build, or each spawned worker re-runs the GUI. No-op from
    # source.
    import multiprocessing
    multiprocessing.freeze_support()
    app = QApplication.instance() or QApplication(sys.argv)
    icon_path = _asset_path("green_wolf.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    check_url = _asset_path("check.svg").resolve().as_posix()
    app.setStyleSheet(_APP_STYLESHEET.replace("{check_icon}", check_url))
    win = FurrifierWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
