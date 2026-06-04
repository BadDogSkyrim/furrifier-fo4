"""Unit tests for the facegen parallel-bake plumbing (worker count + result
accumulation). The bake itself is exercised end-to-end in integration; here we
pin the pure logic."""

from furrifier_fo4.facegen._worker import pick_worker_count, _Result
from furrifier_fo4.facegen import _accumulate


def test_pick_worker_count_throttle_is_one():
    assert pick_worker_count(throttle=True) == 1


def test_pick_worker_count_env_override():
    # Explicit count wins (capped at cpu_count, which is >= 3 on any dev box).
    assert pick_worker_count(throttle=False, env_override="3") == 3


def test_pick_worker_count_bogus_env_falls_back():
    n = pick_worker_count(throttle=False, env_override="not-a-number")
    assert 1 <= n <= 16


def test_pick_worker_count_auto_in_range():
    assert 1 <= pick_worker_count(throttle=False) <= 16


def test_accumulate_folds_results():
    stats = {"baked": 0, "aux": 0, "nif": 0, "nif_failed": 0, "skipped": 0}
    _accumulate(stats, _Result("a", baked=1, aux=2, nif=1, nif_failed=0, skipped=0))
    _accumulate(stats, _Result("b", baked=0, aux=0, nif=0, nif_failed=1, skipped=1))
    assert stats == {"baked": 1, "aux": 2, "nif": 1, "nif_failed": 1, "skipped": 1}
