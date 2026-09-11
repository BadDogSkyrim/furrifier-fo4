"""The cooperative-cancel primitive used by the GUI's Cancel button.

`run`/`build_facegen_for_patch` sample `_check_cancel` at phase boundaries and
per-NPC checkpoints; when the worker's threading.Event is set it raises
CancelledError, which the GUI worker catches to report a clean cancel."""

import threading

import pytest

from furrifier_fo4.session import _check_cancel, CancelledError


def test_check_cancel_noop_when_event_absent_or_unset():
    _check_cancel(None)               # no event -> never cancels (the CLI path)
    _check_cancel(threading.Event())  # event present but unset -> no raise


def test_check_cancel_raises_when_set():
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        _check_cancel(event)
