# Fallout 4 Furrifier

Batch-converts Fallout 4 NPCs to furry races, built on `esplib`. Ships as the
NPC-furrifying tool in the Furry Fallout kit.

Unlike the Skyrim furrifier (≈1:1 vanilla→furry race mapping), Fallout 4 has
essentially one playable race (HumanRace). So furry races are **distributed
across NPC classes by weight**: NPCs are sorted into classes by their
characteristics (race, faction, editor id, name), and each class hands out
furry races according to weights set in a scheme. See `PLAN_FO4_SCHEME.md` (in
the xEditDev root) for the full configuration format.

Furrification is **deterministic**: a given NPC always gets the same result
across runs, as long as the load order and scheme don't change.

## Docs

- **Users:** `FURRIFIER_HOWTO.md` — how to run the tool (the copy shipped beside
  the exe in the kit).
- **Running/testing/building this repo:** `RECIPES.md` — the command reference.
- **Config format:** `PLAN_FO4_SCHEME.md` (xEditDev root).

## What it does

- **Class-distribution engine** (`scheme.py`, `loader.py`) — decides which furry
  race each NPC becomes. Ordered first-match classification, a candidate gate
  (only Human/Ghoul races are furrified — never robots, synths, super mutants,
  turrets, creatures), deterministic weighted distribution, aliases (records that
  ARE one NPC), families (distinct NPCs sharing a race from a leader), and the
  precedence ladder per-NPC > family > class.
- **Record furrification** — RNAM race swap, head parts, and TETI/TEND face
  tints, plus armor/armor-addon race passes.
- **Self-baked FaceGen** — bakes each NPC's face nif + tint-composited textures
  directly (see the tint note below), with optional clone-army variant expansion
  behind leveled lists.
- **Output shaping** — `--pack` into `<patch> - Main.ba2` (GNRL) + `- Textures.ba2`
  (DX10), `--esl` light-plugin flagging, `--no-variants`.
- **GUI** (`gui.py`) — the shipped front end; `python -m furrifier_fo4.gui`.

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

## Tests

Run `python -m pytest` from this directory. See `RECIPES.md` for the full set
(golden CK-parity test, gamefiles-only markers, etc.).
