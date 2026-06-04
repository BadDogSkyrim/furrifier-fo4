"""Unit tests for model helpers — pure, no game files."""

from types import SimpleNamespace

from furrifier_fo4.models import FURRIFIER_AUTHOR, is_furrifier_plugin


def _plugin(author):
    return SimpleNamespace(header=SimpleNamespace(author=author))


def test_is_furrifier_plugin_matches_author():
    assert is_furrifier_plugin(_plugin(FURRIFIER_AUTHOR)) is True


def test_is_furrifier_plugin_rejects_others():
    assert is_furrifier_plugin(_plugin("SomeModder")) is False
    assert is_furrifier_plugin(_plugin(None)) is False


def test_is_furrifier_plugin_handles_none():
    assert is_furrifier_plugin(None) is False
