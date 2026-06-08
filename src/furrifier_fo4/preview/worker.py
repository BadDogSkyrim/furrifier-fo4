"""Background worker for the live-preview pane.

Two phases, both off the GUI thread:

  - **Catalog** (`build_catalog`, ~5s): a partial load of just NPC_/RACE
    groups to list furry-relevant NPCs for the picker. Depends only on
    (data_dir, plugins), not the scheme.
  - **Session** (built lazily inside `bake`, ~10-20s): the full furrification
    session. Built the first time an NPC is actually visualized; emits
    `session_building` so the pane can warn the user. Reused for every later
    bake until the scheme/plugins/data change.

Monotonic request IDs let the pane discard stale bake results.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from .catalog import PreviewCatalog
from .session import PreviewSession

log = logging.getLogger(__name__)


def _key(*parts) -> tuple:
    """Hashable cache key; lists become tuples, None stays None."""
    return tuple(tuple(p) if isinstance(p, list) else p for p in parts)


class PreviewWorker(QObject):
    catalog_building = Signal()
    catalog_ready = Signal(list)          # [(objid, editor_id), ...]
    catalog_failed = Signal(str)
    session_building = Signal()           # heavy load started (first visualize)
    # request_id, nif_path, bake_root, info — info is a dict:
    #   {race, parent_race, breed, editor_id, template_owner, template_count}
    bake_ready = Signal(int, str, str, object)
    bake_failed = Signal(int, str)

    def __init__(self, cache=None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # Shared world cache (the Run worker uses the same one), so the preview
        # session and a full Run share a single plugin load.
        self._cache = cache
        self._catalog: Optional[PreviewCatalog] = None
        self._catalog_key: Optional[tuple] = None
        self._session: Optional[PreviewSession] = None
        self._session_key: Optional[tuple] = None
        self._temp_root: Optional[Path] = None
        self._latest_request_id = 0

    @property
    def session(self) -> Optional[PreviewSession]:
        return self._session

    @Slot(object, object)
    def build_catalog(self, data_dir: Optional[str],
                      plugins: Optional[list] = None) -> None:
        key = _key(data_dir, plugins)
        if self._catalog is not None and self._catalog_key == key:
            self.catalog_ready.emit(self._catalog.entries())
            return
        self.catalog_building.emit()
        try:
            self._catalog = PreviewCatalog(data_dir=data_dir, plugins=plugins)
            self._catalog_key = key
            self.catalog_ready.emit(self._catalog.entries())
        except Exception as exc:
            log.exception("catalog build failed")
            self._catalog = None
            self._catalog_key = None
            self.catalog_failed.emit(str(exc))

    @Slot(str, object, object)
    def reset_session(self, scheme: str, data_dir: Optional[str],
                      plugins: Optional[list] = None) -> None:
        """Drop the heavy session if it no longer matches the given config, so
        the next bake rebuilds it (and re-shows the 'first load' message). The
        catalog is left alone unless plugins/data changed — the pane re-runs
        build_catalog for that."""
        if self._session is not None and self._session_key != _key(
                scheme, data_dir, plugins):
            self._session.close()
            self._session = None
            self._session_key = None

    def _ensure_session(self, scheme: str, data_dir: Optional[str],
                        plugins: Optional[list]) -> PreviewSession:
        key = _key(scheme, data_dir, plugins)
        if self._session is not None and self._session_key == key:
            return self._session
        if self._session is not None:
            self._session.close()
            self._session = None
        self.session_building.emit()
        if self._cache is not None:
            # Reuse the shared world (the Run builds/uses the same one); the
            # PreviewSession just wraps it, so it doesn't own/close the world.
            world = self._cache.get_or_build(scheme, data_dir, plugins)
            self._session = PreviewSession(scheme, world=world)
        else:
            self._session = PreviewSession(scheme, data_dir=data_dir,
                                           plugins=plugins)
        self._session_key = key
        return self._session

    @Slot(int, int, str, object, object, bool, int)
    def bake(self, request_id: int, objid: int, scheme: str,
             data_dir: Optional[str], plugins: Optional[list],
             refurrify: bool, variant: int = 0) -> None:
        self._latest_request_id = max(self._latest_request_id, request_id)
        try:
            session = self._ensure_session(scheme, data_dir, plugins)
            if self._temp_root is None:
                self._temp_root = Path(
                    tempfile.mkdtemp(prefix="fo4_preview_bake_"))
            # Fresh sub-dir per request: the render resolves the head diffuse
            # straight out of this dir, so reusing one path across bakes risks
            # showing a prior bake's texture (e.g. when a file is overwritten
            # with a coarse mtime, or skipped). A unique dir per bake makes a
            # stale read impossible; all subdirs are cleaned on shutdown.
            bake_dir = Path(tempfile.mkdtemp(
                prefix=f"req{request_id}_", dir=self._temp_root))
            result = session.bake(objid, bake_dir, facegen_size=512,
                                  refurrify=refurrify, variant=variant)
            if result is None:
                self.bake_failed.emit(
                    request_id, f"{objid:08X}: not furrifiable under this scheme")
                return
            if request_id != self._latest_request_id:
                return  # a newer request superseded us
            info = {
                "race": result.race_name,
                "parent_race": result.parent_race,
                "breed": result.breed,
                "editor_id": result.editor_id,
                "template_owner": result.template_owner,
                "template_count": result.template_count,
                "template_index": result.template_index,
                "skin_tone": result.skin_tone,
            }
            self.bake_ready.emit(request_id, str(result.nif_path),
                                 str(bake_dir), info)
        except Exception as exc:
            log.exception("bake failed")
            self.bake_failed.emit(request_id, str(exc))

    def shutdown(self) -> None:
        import shutil
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._temp_root is not None:
            shutil.rmtree(self._temp_root, ignore_errors=True)
            self._temp_root = None


@dataclass
class RequestTracker:
    """Monotonic request IDs so the GUI can discard stale bake results."""

    _counter: int = field(default=0)

    def next_id(self) -> int:
        self._counter += 1
        return self._counter

    def is_current(self, request_id: int) -> bool:
        return request_id == self._counter
