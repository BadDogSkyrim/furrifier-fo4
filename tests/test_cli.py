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


def test_direct_construction_gets_esp_suffix():
    # The GUI builds FurrifierConfig() directly (not via from_args), so the
    # extension normalization has to live in the dataclass, not just the parser.
    assert FurrifierConfig(patch_filename="MyPatch").patch_filename == "MyPatch.esp"
    assert FurrifierConfig(patch_filename="MyPatch.esl").patch_filename == "MyPatch.esl"
    assert FurrifierConfig(patch_filename="MyPatch.ESP").patch_filename == "MyPatch.ESP"
    assert FurrifierConfig(patch_filename="  ").patch_filename == "FO4FurryPatch.esp"
    assert FurrifierConfig().patch_filename == "FO4FurryPatch.esp"


def test_esl_flag():
    assert _parse([]).emit_esl is False
    assert _parse(["--esl"]).emit_esl is True


def test_no_variants_flag():
    assert _parse([]).variant_expansion is True
    assert _parse(["--no-variants"]).variant_expansion is False


def test_pack_flag():
    assert _parse([]).pack is False
    assert _parse(["--pack"]).pack is True


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


def test_races_dir_flag():
    assert _parse(["--races", r"C:\frozen\races"]).races_dir == r"C:\frozen\races"
    assert _parse([]).races_dir is None


def test_every_parser_dest_is_copied_by_from_args():
    """`from_args` copies fields one by one, so a newly added flag is easy to
    wire into the parser and then forget to copy across -- which is exactly
    what happened to --races: the flag parsed, and was silently dropped.

    Give every dest a unique sentinel and assert it surfaces on the config.
    Dests that deliberately don't survive verbatim (renamed, inverted, split,
    or consumed before the config) are listed explicitly.
    """
    transformed = {
        "patch",            # -> patch_filename (with .esp normalization)
        "no_facegen",       # -> build_facegen (inverted)
        "no_refurrify",     # -> refurrify_existing (inverted)
        "no_variants",      # -> variant_expansion (inverted)
        "esl",              # -> emit_esl
        "scheme",           # -> race_scheme
        "only_faction",     # -> list, split on commas
        "only_npcs",        # -> list, split on commas
        "help",
    }
    parser = build_parser()
    args = parser.parse_args([])
    sentinels = {}
    for action in parser._actions:
        if action.dest in transformed:
            continue
        sentinels[action.dest] = f"<{action.dest}>"
        setattr(args, action.dest, sentinels[action.dest])

    values = {str(v) for v in FurrifierConfig.from_args(args).__dict__.values()}
    dropped = sorted(d for d, s in sentinels.items() if s not in values)
    assert not dropped, (
        f"flag(s) parsed but never copied onto FurrifierConfig: {dropped}")
