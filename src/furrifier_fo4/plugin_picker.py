"""Modal plugin picker for the FO4 furrifier GUI.

Ported from the Skyrim furrifier. Lists the load order (active pre-checked);
checking a plugin auto-pulls its transitive masters (read from each plugin's
TES4 header). Returns the chosen list, which flows into session.run /
PreviewSession as an explicit `plugins` load order.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QVBoxLayout,
)

from esplib import LoadOrder
from esplib.utils import is_readable_file

PLUGIN_EXTS = {".esp", ".esm", ".esl"}


def read_plugin_masters(path: Path) -> list:
    """Masters declared in a plugin's TES4 header (MAST subrecords).

    Reads only the first 64KB (enough for any TES4 header). Best-effort:
    returns [] on any read/parse error."""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
        if data[:4] != b"TES4":
            return []
        dsize = struct.unpack_from("<I", data, 4)[0]
        pos, end = 24, 24 + dsize
        masters = []
        while pos + 6 <= min(end, len(data)):
            sig = data[pos:pos + 4]
            size = struct.unpack_from("<H", data, pos + 4)[0]
            pos += 6
            chunk = data[pos:pos + size]
            pos += size
            if sig == b"MAST":
                masters.append(chunk.rstrip(b"\x00").decode("cp1252", "replace"))
        return masters
    except Exception:
        return []


class PluginPickerDialog(QDialog):
    """Checkbox list for picking which plugins to run against."""

    def __init__(self, parent, data_dir: Path,
                 initial_selection: Optional[list] = None,
                 exclude: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select plugins")
        self.resize(520, 640)
        self.setModal(True)
        self.result: Optional[list] = None
        self._data_dir = Path(data_dir)
        self._master_cache: dict = {}
        self._exclude = {exclude.lower()} if exclude else set()
        self._user_toggle_in_progress = False

        # Load-order entries with no readable file in data_dir. Filled by
        # _collect_plugins; the active ones get surfaced below.
        self._absent: list = []

        plugins_in_order = self._collect_plugins()
        active = self._active_plugins()
        checked = ({p.lower() for p in initial_selection}
                   if initial_selection is not None else active)

        # An *inactive* plugins.txt entry that isn't there is a stale line
        # and nobody cares. An *active* one is a plugin the game intends to
        # load and we can't see -- either the data dir is wrong or the files
        # aren't reachable from this process. Either way the run will come
        # up short, so say so now rather than let the user discover it as
        # "0 NPCs patched".
        self._missing_active = [n for n in self._absent
                                if n.lower() in active]
        if self._missing_active:
            shown = ", ".join(self._missing_active[:8])
            if len(self._missing_active) > 8:
                shown += f", and {len(self._missing_active) - 8} more"
            logging.getLogger(__name__).warning(
                "%d active plugin(s) from plugins.txt are not present in "
                "%s and were left out of the list: %s",
                len(self._missing_active), self._data_dir, shown)
        self._build_widgets(plugins_in_order, checked)

    # --- plugin lists ------------------------------------------------------

    def _collect_plugins(self) -> list:
        """Ordered plugin list: load-order order, then extras on disk.

        Intersected with what's actually in the data dir. A plugins.txt
        entry we can't read cannot be loaded, so listing it only invites a
        run that reports it missing -- which is exactly the confusion this
        list exists to prevent. Dropped names are kept in `_absent` so the
        caller can say so out loud.

        Enumeration, not stat: under MO2 `iterdir().is_file()` filters out
        every mod-supplied plugin the listing had just found. A DirEntry
        answers is_file() from the enumeration data, with no stat call.
        """
        load_order_names = []
        try:
            load_order_names = list(LoadOrder.from_game("fo4", active_only=False).plugins)
        except Exception:
            pass
        on_disk = []
        try:
            with os.scandir(self._data_dir) as entries:
                for entry in entries:
                    if (Path(entry.name).suffix.lower() in PLUGIN_EXTS
                            and entry.is_file()):
                        on_disk.append(entry.name)
        except OSError:
            pass
        on_disk.sort(key=str.lower)
        on_disk_lower = {n.lower() for n in on_disk}

        present = [n for n in load_order_names if n.lower() in on_disk_lower]
        self._absent = [n for n in load_order_names
                        if n.lower() not in on_disk_lower
                        and n.lower() not in self._exclude]

        seen = {n.lower() for n in present}
        combined = present + [n for n in on_disk if n.lower() not in seen]
        if self._exclude:
            combined = [n for n in combined if n.lower() not in self._exclude]
        return combined

    def _active_plugins(self) -> set:
        try:
            return {n.lower() for n in
                    LoadOrder.from_game("fo4", active_only=True).plugins}
        except Exception:
            return set()

    # --- widgets -----------------------------------------------------------

    def _build_widgets(self, plugins: list, checked: set) -> None:
        layout = QVBoxLayout(self)
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("substring match, case-insensitive")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filt.addWidget(self.filter_edit, stretch=1)
        layout.addLayout(filt)

        self.summary_label = QLabel("", self)
        layout.addWidget(self.summary_label)

        # Absent-but-active plugins get a standing banner rather than a
        # modal -- the user needs it while looking at the list, not as
        # something to click past before seeing it.
        if self._missing_active:
            warn = QLabel(
                f"⚠ {len(self._missing_active)} active plugin(s) in "
                f"plugins.txt are not in this data directory and can't be "
                f"loaded. Hover for the list.", self)
            warn.setWordWrap(True)
            # Cap the tooltip -- when the data dir is wrong this list is the
            # entire load order, and a 250-line tooltip is a wall.
            names = self._missing_active[:40]
            if len(self._missing_active) > 40:
                names.append(f"... and {len(self._missing_active) - 40} more")
            warn.setToolTip("\n".join(names))
            warn.setObjectName("pluginWarning")
            layout.addWidget(warn)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        for name in plugins:
            item = QListWidgetItem(name, self.list_widget)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setCheckState(Qt.CheckState.Checked if name.lower() in checked
                               else Qt.CheckState.Unchecked)
        layout.addWidget(self.list_widget, stretch=1)

        row = QHBoxLayout()
        reset = QPushButton("Reset", self)
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        row.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("OK", self)
        ok.clicked.connect(self._on_ok)
        ok.setDefault(True)
        row.addWidget(ok)
        layout.addLayout(row)
        self._update_summary()

    # --- helpers -----------------------------------------------------------

    def _all_items(self) -> list:
        return [self.list_widget.item(i) for i in range(self.list_widget.count())]

    def _visible_items(self) -> list:
        return [it for it in self._all_items() if not it.isHidden()]

    def _by_name_lower(self, name: str):
        target = name.lower()
        for it in self._all_items():
            if it.data(Qt.ItemDataRole.UserRole).lower() == target:
                return it
        return None

    def _apply_filter(self) -> None:
        q = self.filter_edit.text().strip().lower()
        for it in self._all_items():
            it.setHidden(bool(q) and q not in
                         it.data(Qt.ItemDataRole.UserRole).lower())
        self._update_summary()

    def _update_summary(self) -> None:
        total = self.list_widget.count()
        checked = sum(1 for it in self._all_items()
                      if it.checkState() == Qt.CheckState.Checked)
        visible = len(self._visible_items())
        self.summary_label.setText(
            f"{checked} / {total} checked"
            + ("" if visible == total else f" ({visible} shown)"))

    def _get_masters(self, name: str) -> list:
        key = name.lower()
        if key not in self._master_cache:
            path = self._data_dir / name
            self._master_cache[key] = (read_plugin_masters(path)
                                       if is_readable_file(path) else [])
        return self._master_cache[key]

    def _pull_in_masters(self, name: str) -> None:
        seen, queue = set(), [name]
        while queue:
            for master in self._get_masters(queue.pop()):
                key = master.lower()
                if key in seen:
                    continue
                seen.add(key)
                item = self._by_name_lower(master)
                if item is not None and item.checkState() != Qt.CheckState.Checked:
                    self._user_toggle_in_progress = True
                    try:
                        item.setCheckState(Qt.CheckState.Checked)
                    finally:
                        self._user_toggle_in_progress = False
                queue.append(master)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._user_toggle_in_progress:
            self._update_summary()
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._pull_in_masters(item.data(Qt.ItemDataRole.UserRole))
        self._update_summary()

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        for label, slot in (("&Check all", self._check_all),
                            ("Unch&eck all", self._uncheck_all),
                            ("&Invert selection", self._invert)):
            act = QAction(label, menu)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.exec(self.list_widget.mapToGlobal(pos))

    def _check_all(self) -> None:
        for it in self._visible_items():
            it.setCheckState(Qt.CheckState.Checked)

    def _uncheck_all(self) -> None:
        for it in self._visible_items():
            it.setCheckState(Qt.CheckState.Unchecked)

    def _invert(self) -> None:
        for it in self._visible_items():
            it.setCheckState(
                Qt.CheckState.Unchecked
                if it.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked)

    def _reset(self) -> None:
        active = self._active_plugins()
        for it in self._all_items():
            name = it.data(Qt.ItemDataRole.UserRole)
            it.setCheckState(Qt.CheckState.Checked if name.lower() in active
                             else Qt.CheckState.Unchecked)

    def _on_ok(self) -> None:
        self.result = [it.data(Qt.ItemDataRole.UserRole)
                       for it in self._all_items()
                       if it.checkState() == Qt.CheckState.Checked]
        self.accept()
