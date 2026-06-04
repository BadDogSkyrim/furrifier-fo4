# Fallout 4 Furrifier

Batch-converts Fallout 4 NPCs to furry races, built on `esplib`.

Unlike the Skyrim furrifier (≈1:1 vanilla→furry race mapping), Fallout 4 has
essentially one playable race (HumanRace). So furry races are **distributed
across NPC classes by weight**: NPCs are sorted into classes by their
characteristics (race, faction, editor id, name), and each class hands out
furry races according to weights you set. See `PLAN_FO4_SCHEME.md` (in the
xEditDev root) for the full configuration format.

Furrification is **deterministic**: a given NPC always gets the same result
across runs, as long as the load order and scheme don't change.

## Status

Under construction. Done so far:

- **G1 — class-distribution engine** (`scheme.py`, `loader.py`): the core that
  decides which furry race each NPC becomes. Ordered first-match
  classification, a candidate gate (only Human/Ghoul races are ever
  furrified — never robots, synths, super mutants, turrets, creatures),
  deterministic weighted distribution, aliases (records that ARE one NPC),
  families (distinct NPCs sharing a race from a leader), and the precedence
  ladder per-NPC > family > class. Fully unit-tested.

Still to come: G2 data loading (read FO4 RACE/HDPT/CLFM appearance data),
G3 NPC/race furrification (RNAM swap, headparts, TETI/TEND tints), G4 armor
passes, and G5 self-baked FaceGen with real tint-layer composition.

## Configuration

Two files next to the executable:

- `builtin.toml` — base-game NPC classes, aliases, and families. Rarely needs
  editing.
- `schemes/<name>.toml` — your scheme: which furry races each class gets, and
  any per-NPC overrides. Pick one at runtime with `--scheme <name>`.

## Tints & blend modes

The furrifier bakes each NPC's face texture itself, and it **honors the blend
operation** set on every RACE tint-color preset (Default, Multiply, Overlay,
Soft Light, Hard Light). This matters because **the Creation Kit ignores the
blend mode when it bakes faces — the furrifier does not.** So blend ops you
author in a race def take effect in furrified faces even though CK would bake
them flat. For example, Multiply makes a black marking stay black over any fur
(use it for stripes); Hard Light makes a white marking stay white (use it for
white markings — Multiply does nothing to white).

Author these blend ops in **xEdit, not the Creation Kit** — CK silently resets
a preset's blend operation back to Default on save.

Run the tests with `python -m pytest` from this directory.
