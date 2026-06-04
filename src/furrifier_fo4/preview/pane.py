"""Live preview pane: picker + worker + 3D viewer in one QWidget.

On first show, the worker builds a fast NPC *catalog* (~5s, partial load) and
fills the picker — no button to press. The first time an NPC is visualized the
worker builds the full furrification *session* (~10-20s) and shows a one-time
notice; later picks bake in well under a second. Changing the scheme drops the
session silently (the next visualize rebuilds it); changing plugins also
refreshes the picker. The pane reads the current config via `config_provider`,
so scheme / plugins / refurrify changes propagate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..config import FurrifierConfig
from .npc_picker import NpcEntry, NpcPickerWidget
from .scene_widget import FacegenSceneWidget
from .worker import PreviewWorker, RequestTracker

log = logging.getLogger(__name__)

ConfigProvider = Callable[[], FurrifierConfig]


@dataclass
class _HistoryEntry:
    objid: int
    nif_path: Optional[Path] = None
    bake_root: Optional[Path] = None
    info: Optional[dict] = None      # last bake's template info, for nav restore


class PreviewPane(QWidget):
    """Vertical stack: picker/nav row + 3D viewer + footer."""

    _dispatch_catalog = Signal(object, object)            # data_dir, plugins
    _dispatch_reset = Signal(str, object, object)         # scheme, data, plugins
    _dispatch_bake = Signal(int, int, str, object, object, bool, bool)
    # request_id, objid, scheme, data_dir, plugins, refurrify, roll

    def __init__(self, config_provider: ConfigProvider,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config_provider = config_provider
        self._tracker = RequestTracker()
        self._last_objid: Optional[int] = None
        self._history: list = []
        self._history_pos: int = -1
        self._history_cap: int = 20
        self._reset_camera_next: bool = True
        self._catalog_loaded: bool = False

        _nav_qss = "QPushButton { font-size: 18pt; padding: 0px; margin: 0px; }"
        self.back_button = QPushButton("◀", self)
        self.back_button.setFixedSize(32, 32)
        self.back_button.setEnabled(False)
        self.back_button.setStyleSheet(_nav_qss)
        self.back_button.clicked.connect(self._on_back)
        self.forward_button = QPushButton("▶", self)
        self.forward_button.setFixedSize(32, 32)
        self.forward_button.setEnabled(False)
        self.forward_button.setStyleSheet(_nav_qss)
        self.forward_button.clicked.connect(self._on_forward)
        self.reframe_button = QPushButton("Reframe", self)
        self.reframe_button.setEnabled(False)
        self.reframe_button.clicked.connect(lambda: self.scene.reframe_camera())
        # Roll: re-sample a templated NPC's appearance (random owner + random
        # signature). Only meaningful — and only enabled — for NPCs whose look
        # is inherited from a template. See _on_bake_ready.
        self.roll_button = QPushButton("Roll", self)
        self.roll_button.setEnabled(False)
        self.roll_button.setToolTip(
            "This NPC inherits its look from a template — roll a different "
            "possible face")
        self.roll_button.clicked.connect(self._on_roll)

        self.picker = NpcPickerWidget(self)
        self.picker.setEnabled(False)
        self.picker.npc_selected.connect(self._on_npc_picked)

        self.scene = FacegenSceneWidget(self)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scene.setSizePolicy(sp)

        self.status_label = QLabel("Finding NPCs…", self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("QLabel { font-weight: bold; }")
        # Banner shown only for templated NPCs — names the inherited trait-owner
        # + how many distinct faces the NPC could show in-game.
        self.template_label = QLabel("", self)
        self.template_label.setWordWrap(True)
        self.template_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.template_label.setStyleSheet(
            "QLabel { color: #4CC94C; font-size: 9pt; font-style: italic; }")
        self.template_label.hide()
        self.headparts_label = QLabel("", self)
        self.headparts_label.setWordWrap(True)
        self.headparts_label.setStyleSheet("QLabel { color: #888; font-size: 9pt; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        nav = QHBoxLayout()
        nav.addWidget(self.back_button)
        nav.addWidget(self.picker, stretch=1)
        nav.addWidget(self.forward_button)
        nav.addWidget(self.reframe_button)
        nav.addWidget(self.roll_button)
        layout.addLayout(nav)
        layout.addWidget(self.status_label)
        layout.addWidget(self.template_label)
        layout.addWidget(self.scene, stretch=1)
        layout.addWidget(self.headparts_label)

        # Worker on its own thread.
        self._thread = QThread(self)
        self._worker = PreviewWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()
        self._worker.catalog_building.connect(self._on_catalog_building)
        self._worker.catalog_ready.connect(self._on_catalog_ready)
        self._worker.catalog_failed.connect(self._on_catalog_failed)
        self._worker.session_building.connect(self._on_session_building)
        self._worker.bake_ready.connect(self._on_bake_ready)
        self._worker.bake_failed.connect(self._on_bake_failed)
        self._dispatch_catalog.connect(self._worker.build_catalog)
        self._dispatch_reset.connect(self._worker.reset_session)
        self._dispatch_bake.connect(self._worker.bake)

    # ----- lifecycle / config changes --------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._catalog_loaded:
            self._catalog_loaded = True
            self._reload_catalog()

    def _reload_catalog(self) -> None:
        config = self._config_provider()
        self._dispatch_catalog.emit(config.data_dir, config.plugins)

    def on_scheme_changed(self) -> None:
        """Scheme changed: drop the loaded session (the next visualize rebuilds
        it and re-shows the one-time notice). The picker list is scheme-
        independent, so it stays."""
        self._reset_view()
        config = self._config_provider()
        self._dispatch_reset.emit(config.race_scheme, config.data_dir,
                                  config.plugins)

    def on_load_order_changed(self) -> None:
        """Plugins or data dir changed: the NPC set may differ, so rebuild the
        picker, and drop the session."""
        self._reset_view()
        self.picker.set_entries([])
        self.picker.setEnabled(False)
        self.status_label.setText("Finding NPCs…")
        self._catalog_loaded = True
        config = self._config_provider()
        self._dispatch_reset.emit(config.race_scheme, config.data_dir,
                                  config.plugins)
        self._reload_catalog()

    def on_refurrify_changed(self) -> None:
        """Re-bake the current NPC so the toggle takes visible effect."""
        if 0 <= self._history_pos < len(self._history):
            self._history[self._history_pos].nif_path = None  # force re-bake
            self._dispatch_bake_for_current()

    def _reset_view(self) -> None:
        """Clear the displayed head + navigation history (not the picker)."""
        self._tracker.next_id()  # discard any in-flight bake
        self._history = []
        self._history_pos = -1
        self._last_objid = None
        self._reset_camera_next = True
        self.back_button.setEnabled(False)
        self.forward_button.setEnabled(False)
        self.reframe_button.setEnabled(False)
        self.roll_button.setEnabled(False)
        self.template_label.hide()
        self.scene.clear()
        self.headparts_label.setText("")

    # ----- picker / navigation ---------------------------------------------

    def _on_npc_picked(self, objid: int) -> None:
        for i, entry in enumerate(self._history):
            if entry.objid == objid:
                if i == self._history_pos and entry.nif_path is not None:
                    return
                self._history_pos = i
                self._navigate_to_current()
                return
        del self._history[self._history_pos + 1:]
        self._history.append(_HistoryEntry(objid=objid))
        if len(self._history) > self._history_cap:
            self._history = self._history[len(self._history) - self._history_cap:]
        self._history_pos = len(self._history) - 1
        self._update_nav_buttons()
        self._dispatch_bake_for_current()

    def _dispatch_bake_for_current(self, roll: bool = False) -> None:
        if self._history_pos < 0:
            return
        entry = self._history[self._history_pos]
        self._last_objid = entry.objid
        request_id = self._tracker.next_id()
        self.status_label.setText(
            f"Rolling {entry.objid:08X}…" if roll else f"Baking {entry.objid:08X}…")
        config = self._config_provider()
        self._dispatch_bake.emit(request_id, entry.objid, config.race_scheme,
                                 config.data_dir, config.plugins,
                                 config.refurrify_existing, roll)

    def _on_roll(self) -> None:
        """Re-sample the current templated NPC: a random reachable owner on a
        random signature. No history change — it replaces the shown face."""
        if 0 <= self._history_pos < len(self._history):
            self._history[self._history_pos].nif_path = None  # force re-bake
            self._dispatch_bake_for_current(roll=True)

    def _on_back(self) -> None:
        if self._history_pos > 0:
            self._history_pos -= 1
            self._navigate_to_current()

    def _on_forward(self) -> None:
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self._navigate_to_current()

    def _navigate_to_current(self) -> None:
        self._update_nav_buttons()
        entry = self._history[self._history_pos]
        self._last_objid = entry.objid
        if entry.nif_path is not None and entry.nif_path.is_file():
            self._update_template_banner(entry.info or {})
            self._show(entry.nif_path, entry.bake_root, preserve=True)
        else:
            self._dispatch_bake_for_current()

    def _update_nav_buttons(self) -> None:
        self.back_button.setEnabled(self._history_pos > 0)
        self.forward_button.setEnabled(
            self._history_pos < len(self._history) - 1)

    # ----- worker signals --------------------------------------------------

    def _on_catalog_building(self) -> None:
        self.status_label.setText("Finding NPCs…")
        self.picker.setEnabled(False)

    def _on_catalog_ready(self, entries: list) -> None:
        npc_entries = [NpcEntry(form_id=objid, editor_id=edid)
                       for objid, edid in entries]
        self.picker.set_entries(npc_entries)
        self.picker.setEnabled(True)
        self.status_label.setText(
            f"{len(npc_entries)} NPCs — pick one to preview.")

    def _on_catalog_failed(self, message: str) -> None:
        self.status_label.setText(f"Couldn't list NPCs: {message}")

    def _on_session_building(self) -> None:
        # The heavy load only runs the first time after launch or a config
        # change; warn the user it's a one-time wait.
        self.status_label.setText("First preview — loading the world (10-20s)…")

    def _on_bake_ready(self, request_id: int, nif_path: str,
                       bake_root: str, info: object) -> None:
        if not self._tracker.is_current(request_id):
            return
        nif = Path(nif_path)
        root = Path(bake_root) if bake_root else None
        info = info if isinstance(info, dict) else {}
        if 0 <= self._history_pos < len(self._history):
            entry = self._history[self._history_pos]
            if entry.objid == self._last_objid:
                entry.nif_path = nif
                entry.bake_root = root
                entry.info = info
        self._update_template_banner(info)
        self._show(nif, root, preserve=not self._reset_camera_next)

    def _update_template_banner(self, info: dict) -> None:
        """Show the inherited-from-template banner + enable Roll, for a
        templated NPC; hide both otherwise. Roll steps through the owners, so
        the banner shows the current position (face X of N)."""
        owner = info.get("template_owner")
        count = info.get("template_count") or 0
        if owner and count:
            race = info.get("race") or "?"
            index = info.get("template_index") or 0
            if count == 1:
                self.template_label.setText(
                    f"Inherited from template: {owner} → {race} "
                    "(only 1 possible face — shared template)")
                self.roll_button.setEnabled(False)
            else:
                self.template_label.setText(
                    f"Inherited from template: {owner} → {race} "
                    f"(face {index + 1} of {count} — Roll to step)")
                self.roll_button.setEnabled(True)
            self.template_label.show()
        else:
            self.template_label.hide()
            self.roll_button.setEnabled(False)

    def _on_bake_failed(self, request_id: int, message: str) -> None:
        if not self._tracker.is_current(request_id):
            return
        self.template_label.hide()
        self.roll_button.setEnabled(False)
        self.status_label.setText(f"Bake failed: {message}")

    # ----- helpers ---------------------------------------------------------

    def _show(self, nif_path: Path, bake_root: Optional[Path],
              preserve: bool) -> None:
        config = self._config_provider()
        data_dir = config.data_dir
        if not data_dir:
            from esplib import find_game_data
            try:
                data_dir = str(find_game_data("fo4"))
            except Exception:
                self.status_label.setText("No data dir — can't resolve textures")
                return
        edid = self._editor_id_for(self._last_objid)
        self.status_label.setText(
            f"{edid} ({self._last_objid:08X})" if edid else nif_path.name)
        self._reset_camera_next = False
        try:
            self.scene.set_nif(nif_path, Path(data_dir), bake_root=bake_root,
                               preserve_camera=preserve)
            self.reframe_button.setEnabled(True)
        except Exception as exc:
            log.exception("scene load failed")
            self.status_label.setText(f"Scene load failed: {exc}")
        self._update_headparts_label(nif_path)

    def _editor_id_for(self, objid: Optional[int]) -> Optional[str]:
        if objid is None:
            return None
        for entry in self.picker.entries():
            if entry.form_id == objid:
                return entry.editor_id
        return None

    def _update_headparts_label(self, nif_path: Path) -> None:
        from .._pyn import ensure_dev_path
        ensure_dev_path()
        try:
            from pyn.pynifly import NifFile
            names = [s.name for s in NifFile(str(nif_path)).shapes]
        except Exception:
            self.headparts_label.setText("")
            return
        self.headparts_label.setText("Headparts: " + ", ".join(sorted(names)))

    # ----- lifecycle -------------------------------------------------------

    def shutdown(self) -> None:
        self._worker.shutdown()
        self._thread.quit()
        self._thread.wait(2000)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
