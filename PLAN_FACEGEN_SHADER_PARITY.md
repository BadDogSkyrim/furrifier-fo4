# Plan: FaceGen shader/export parity with the CK

## Problem

We keep discovering facegen shader mismatches **one at a time, by eye, in-game**
(shader type demote, FACE flag on horns, missing cloth block, height scale, now
`skinTintColor` on the horn base). Each is found only after a bake looks wrong in
the game.

Root cause of the *process*: the export tests in `tests/test_facegen_assemble.py`
are **synthetic** — they build a fake source with an inline shader (no BGSM), bake
it, and assert a handful of invariants we already knew about (SKINNED added, FACE
kept on the head, FACE stripped off a non-face part, shader type from material).
They **never compare a real bake against a real CK bake**, and they only check the
specific bits we named. So anything we didn't think to assert drifts silently:
`skinTintColor`, `EYE_ENVIRONMENT_MAPPING`, `TRANSFORM_CHANGED`, F2 generally, etc.
The tests encode *our assumptions*, which is exactly where the bugs are.

## Current known diffs vs CK (John 00002CCB, baked as deer via single_race)

| shape / property | ours | CK | note |
|---|---|---|---|
| all shapes — F2 `0x80` | unset | `TRANSFORM_CHANGED` | transient/runtime flag |
| eyes — F1 `0x20000` | `EYE_ENVIRONMENT_MAPPING` set | unset | strip like FACE-on-horns |
| `FFOHornBase01.skinTintColor` | `1.0, 1.0, 1.0` (white) | `0.694, 0.494, 0.361` | NPC skin tone; **the horn-base color bug** |

Everything else on John now matches the CK (horns FACE-stripped, head keeps FACE,
shader types, textures, bones/scale, cloth block on the hair shape).

## Plan

### Phase 1 — Golden CK-parity test (fix the process)
Turn "whack-a-mole in-game" into "one failing test that lists every diff."

- [ ] Commit two CK reference facegeom into `tests/fixtures/` (`John_CK_00002CCB.nif` from "C:\tmp\John_CK\00002CCB.nif" and `Rosalind_CK_0005E562.nif` from "C:\tmp\Rosalind_CK\0005E562.nif"). John is a deer with horns and horn base; Rosalind has cloth dynamics on hair.
- [ ] Commit the source headparts for these nifs. We may need the tri files as well; we will need the materials files. The files have to go where needed for the paths to resolve, so nifs go to `tests/fixtures/meshes`, while the textures and materials go to `.../textures/` and `.../materials/` with correct relative file paths. This way the facegen code works as intended with no tweaking.
  - [ ] For John:
    - [ ] Head from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Deer\DeerMaleHead.nif` (with tri files if needed)
    - [ ] Hair from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Hair\Male\Hair05_Hor.nif`
    - [ ] Mouth from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Deer\Mouth\DeerMaleMouth.nif`
    - [ ] Eyes from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Eyes\HorseEyes.nif`
    - [ ] Horns from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Deer\Horns\Horns01.nif`
    - [ ] Horn base from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Deer\Horns\HornsBase01.nif`
  - [ ] For Rosalind, 
    - [ ] Head from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Fox\FoxFemaleHead.nif`
    - [ ] Hair from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Hair\Female\FemaleHair04_LyTi.nif`
    - [ ] Eyes from `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets\Meshes\FFO\Eyes\PredatorFemaleEyes.nif`
- [ ] Add a `@pytest.mark.gamefiles` test for John and Rosalind that bakes the NPC (using a `test_facegen` scheme that forces John to FFODeerRace and Rosalind to FFOFoxRace)
      and diffs our output against the CK known-good reference **shape-by-shape**:
      `Shader_Type`, all flags in `Shader_Flags_1` and `Shader_Flags_2`, textures, `skinTintColor` and skin tint alpha, rimlight power, backlight power, specular color and strength, root material, vertex colors, skin-instance type, bone transforms (incl. scale), presence of cloth data on the correct TriShape.
- Certain fields are set during facegen and are expected to differ from the source so we don't compare back to the source nifs. We compare against the CK reference.

### Phase 2 — Fix what Phase 1 surfaces (known items)
1. [ ] **skinTintColor** — for Skin-Tint (type 5) shapes, write the NPC's skin-tone
       RGB and alpha (the same tone the face/head uses) instead of leaving white. Fixes the
       horn-base color. The skin tone is derived from the skin tone tint layer.
2. [ ] **EYE_ENVIRONMENT_MAPPING (F1 0x20000)** — set according to the source. Note this may be the source's materials file.
3. [ ] **TRANSFORM_CHANGED (F2 0x80)** — set it to match CK byte-for-byte. (I don't know what it does, but CK sets it so we should too -Hugh)

### Phase 3 — Derive flags by rule, not copy-and-patch
- [ ] Replace the current `_copy_shape` approach ("copy the source shader wholesale,
      then patch each bit we've found is wrong") with an explicit CK-derived policy
      keyed on shader type / part role, so the output is correct by construction and
      the golden test stays green as new parts/races appear.
- We determine how to handle a headpart by looking at its shader type only.
  - Face_Tint = diffuse texture path is the generated face. FACE shader flag must be set.
  - Skin_Tint = generated from the NPC's skin tone layer
  - Environment_Map = Environment map bit must be set regardless of value in source. If the Eye_Environment_Map bit is set in the materials file, the corresponding shader flag must be set in the generated nif
  - Default = no special handling

## Design decisions

- We run the furrifier/facegen with a minimal plugin list - base game and DLCs plus FurryFallout.esp. Tests are dependent on those plugins being in the game folder.
- The test scheme referenced above can live in the usual folder but is excluded from the distribution kit.
- The destination for the furrify run can be an output folder for test results. This should not be checked in but should not be deleted after the test either, so test results can be inspected by hand.
- We need a way to run the furrifier against specific NPCs so we can test John and Rosalind without having to furrify everyone.
- We need a way to specify where the resources come from, overriding the game folder. We could reuse the --data-dir setting, with the semantics that resources are looked for first in that folder, then the game folder if not found. This would cover both plugins and other assets, which is fine. There might be some use to it - you could point --data-dir at a mod you haven't installed yet. We could also have a separate flag like --resources, but I like the simplicity of having one mechanism.
- Really, we should test that we're producing a correct facegen diffuse so really, we should also copy diffuse textures into our test fixtures and compare the diffuse output. But let's not do that now, since we aren't having a problem with that. 

## STATUS: Phase 1 + Phase 2 DONE (2026-06-18)

Golden test `tests/test_facegen_parity.py` PASSES for John + Rosalind — our bake
matches the CK across shader type, all F1/F2 flags, textures, specular, backlight,
skin tint, root material, bones (incl. scale), and cloth attachment. Suites green
(furrifier 214, esplib 385).

What got built/fixed:
- **CLI:** `--resources` (was `--data-dir`, now primary→game fallback for plugins +
  assets, threaded through LoadOrder/AssetResolver/FacialBoneRegions/world AND the
  bake workers), `--faction` (was `--only-faction`), `--npcs` EditorID filter,
  `test_facegen` scheme (kit-excluded).
- **Golden test:** drives `main()` through the front door, bakes from committed
  `tests/fixtures/` (11 headpart nifs + .bgsm + 2 CK refs), diffs each shape vs CK.
  Bone geometry compared with tolerance; skin-tint alpha with a small tolerance.
- **Phase 2 fixes (assemble._copy_shape):**
  - specular (`Spec_Color`/`Spec_Str`) + `backlightPower` derived from the BGSM
    MATERIAL (CK does; the nif's inline block is zeroed by FFO).
  - NPC skin tone (from QNAM) baked into Skin-Tint (type 5) `skinTintColor` — the
    horn-base colour.
  - `TRANSFORM_CHANGED` (F2 0x80) set on every shape.
  - root material left NONE (no RootMaterialPath) ≡ CK's empty.
- **Test-policy divergences (deliberate, asserted not blind-masked):**
  - head Normal/Specular use the SHARED race maps (not CK's per-NPC _msn/_s) —
    asserted against the expected shared maps.
  - eyes preserve the modder's source Normal/Specular + EYE_ENVIRONMENT_MAPPING
    (CK substitutes its own) — masked.

### Phase 3 — DONE (2026-06-18), pending in-game confirmation
`_copy_shape`'s shader handling restructured into: UNIVERSAL fixups (drop refs,
SKINNED, material-derived specular+backlight, TRANSFORM_CHANGED) + a dispatch on
the FINAL shader TYPE:
- Face Tint -> FACE flag (every other type has FACE cleared — replaces the old
  `is_face` check; for FFO, face material has `facegen` so type==FACE_TINT ⟺ the
  face part, and `is_face` is dropped from `_copy_shape`).
- Skin Tint -> skinTintColor from the NPC skin tone.
- Glow / Default / Environment Map -> no extra flags (eyes keep the modder's
  env-map setup verbatim).
Behaviour preserved: golden test green + full suite (215) green. Also hardened
`_apply_material_shading` (getattr-guarded) after a regression where an effect
(BGEM) material lacking BGSM fields threw and failed 18 nifs in single_race —
now a unit test (`test_apply_material_shading_skips_missing_fields`).
MUST be confirmed in game (the golden test only covers John + Rosalind; a
refactor can shift behaviour for races/shapes not in the test).

## Done this round (context)
- Horn FACE flag stripped from non-face parts (`_copy_shape`, `is_face`).
- Race per-sex height scale on bone transforms (`esplib.race_height`).
- Cloth `BSClothExtraData` copied onto the hair shape (+ NiflyDLL `setClothExtraData`
  shaperef fix — DLL rebuilt).
- Shader type from material `facegen`/`skinTint`/`glowmap` flags.
