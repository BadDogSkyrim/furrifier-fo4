# Frozen race catalog for the CK-parity golden test

`test_facegen_parity.py` bakes John + RosalindOrman and diffs every shape
against a committed CK reference nif. The bake reads tint colours, alphas and
face morphs from a race catalog — so if that test used the SHIPPING
`races/ffo_races.toml`, any colour tweak would change the bake and break the
golden test with no code change at all. (It did: ranging the fur alphas moved
John's `FFOHornBase01.skin_tint_alpha` from 0.898 to 0.83.)

So the test passes `--races <this dir>` and bakes against this frozen copy,
which is the catalog the committed CK reference was produced from.

**Do not "update" this file to match the shipping catalog.** It is only valid
alongside `../ck_reference/*.nif`. If you deliberately change what the CK
reference should look like, re-bake the reference in the Creation Kit and
replace BOTH together.
