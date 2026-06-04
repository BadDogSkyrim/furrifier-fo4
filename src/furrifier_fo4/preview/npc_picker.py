"""Type-ahead NPC picker widget.

An editable QComboBox with a filtered completer. The user types part
of an editor ID; the completer narrows the list of matching NPCs and
emits a signal when a choice is committed.

The NPC list gets populated once (from `session.plugin_set`) — the
list is thousands of rows on a real load order, so we keep a single
backing list and filter against it rather than re-enumerating on
every keystroke.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt, QStringListModel, Signal
from PySide6.QtWidgets import QComboBox, QCompleter


@dataclass(frozen=True)
class NpcEntry:
    """A pickable NPC: form_id for identification, editor_id for
    filtering + display, optional breed for visual flavor disambiguation.
    Equality is by form_id."""
    form_id: int
    editor_id: str
    breed_name: Optional[str] = None

    def label(self) -> str:
        if self.breed_name:
            return (f"{self.editor_id} — {self.breed_name}  "
                    f"({self.form_id:08X})")
        return f"{self.editor_id}  ({self.form_id:08X})"


class NpcPickerWidget(QComboBox):
    """Editable combobox with a typeahead completer over NPC editor IDs.

    Emits `npc_selected(form_id)` when the user commits a choice
    (either pressing Enter in the edit field or picking from the
    dropdown).
    """

    npc_selected = Signal(int)  # emits NpcEntry.form_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Large list (thousands of NPCs) — cap visible rows so the
        # dropdown doesn't explode vertically.
        self.setMaxVisibleItems(20)

        self._entries: list[NpcEntry] = []

        # Completer drives the typeahead. MatchContains = substring
        # match (not just prefix), case-insensitive.
        self._completer_model = QStringListModel(self)
        self._completer = QCompleter(self._completer_model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self.setCompleter(self._completer)

        self.activated.connect(self._on_activated)
        self.lineEdit().returnPressed.connect(self._on_return)

    def set_entries(self, entries: List[NpcEntry]) -> None:
        """Populate the picker from an NPC list. Previous state is
        cleared. Editor IDs feed both the dropdown display and the
        completer."""
        self._entries = list(entries)
        self.clear()
        labels = [e.label() for e in self._entries]
        self.addItems(labels)
        self._completer_model.setStringList(labels)
        self.setCurrentIndex(-1)
        self.lineEdit().clear()

    def entries(self) -> List[NpcEntry]:
        return list(self._entries)

    # ----- selection handling ----------------------------------------------

    def _on_activated(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            self.npc_selected.emit(self._entries[index].form_id)

    def _on_return(self) -> None:
        """Map the line-edit text to an entry and emit. Accepts either
        a full label or the bare editor_id (case-insensitive)."""
        text = self.lineEdit().text().strip()
        entry = self._lookup(text)
        if entry is not None:
            self.npc_selected.emit(entry.form_id)

    def _lookup(self, text: str) -> Optional[NpcEntry]:
        if not text:
            return None
        lower = text.lower()
        # Exact label match first.
        for e in self._entries:
            if e.label().lower() == lower:
                return e
        # Editor ID match next.
        for e in self._entries:
            if e.editor_id.lower() == lower:
                return e
        # Last resort: the first substring hit.
        for e in self._entries:
            if lower in e.editor_id.lower():
                return e
        return None
