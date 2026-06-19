"""CLI config parsing — pure, no game files."""

from furrifier_fo4.config import FurrifierConfig, build_parser, normalize_argv


def _parse(argv):
    args = build_parser().parse_args(normalize_argv(argv))
    return FurrifierConfig.from_args(args)


def test_defaults():
    c = _parse([])
    assert c.patch_filename == "FO4FurryPatch.esp"
    assert c.race_scheme == "user"
    assert c.build_facegen is True
    assert c.facegen_size == 1024
    assert c.only_faction is None
    assert c.refurrify_existing is True


def test_no_refurrify_flag():
    assert _parse([]).refurrify_existing is True
    assert _parse(["--no-refurrify"]).refurrify_existing is False


def test_patch_gets_esp_suffix():
    assert _parse(["--patch", "MyPatch"]).patch_filename == "MyPatch.esp"
    assert _parse(["--patch", "MyPatch.esl"]).patch_filename == "MyPatch.esl"


def test_faction_splits_and_trims():
    c = _parse(["--faction", "A, B ,C"])
    assert c.only_faction == ["A", "B", "C"]


def test_npcs_splits_and_trims():
    c = _parse(["--npcs", "John, RosalindOrman "])
    assert c.only_npcs == ["John", "RosalindOrman"]


def test_no_facegen_and_size():
    c = _parse(["--no-facegen", "--facegen-size", "2048"])
    assert c.build_facegen is False
    assert c.facegen_size == 2048


def test_switch_case_insensitive():
    c = _parse(["--Patch", "X", "--LIMIT", "5"])
    assert c.patch_filename == "X.esp"
    assert c.limit == 5
