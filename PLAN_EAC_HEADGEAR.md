# PLAN — FFO patch for Eli's Armour Compendium (EAC) headgear

Status: **DONE** (2026-06-30). Full run shipped to `…\mods\Sandbox\`, Hugh
validated in-game. Reusable implementation: `furrifier_fo4/headgear_patch.py`
(edit its CONFIG block for another mod).

## Resolved / verified during build
- **Offset (skin space): (0.002, −0.075, +2.001)** — mean vertex delta of the
  Baseball MHat pair. Single global constant (Hugh: "all furry heads take the
  same repositioning, by design").
- **Headpart detection (per shape):** a shape that is a `BSSubIndexTriShape`,
  skinned to a `HEAD*` bone, with a non-empty `.ssf` and FO4 segments ⊆ {0,1}.
  (EAC bundles a goggles/card sub-shape with segs 0–3 — that's why detection must
  be per-shape, not max-over-nif.)
- **Full-head exception = ARMA BOD2 occupies slot 32 (FaceGen Head).** Verified:
  the gasmask/captain hoods + VaultTec helmets have slot 32; real hats don't.
- **Reposition method:** PyNifly has no vertex setter, so shift each bone's
  `skin_to_bone` translation by `(skin_to_bone.rotation · Δ)` (provably identical
  to translating skin-space verts; preserves all vertex data). Non-skinned shapes
  → shift the shape transform. VALIDATED: repositioning vanilla MHat reproduces
  the FFO furry MHat to 0.27 max effective-vertex diff (= vertex-compression noise).
- **Target headparts (slot ≠ 32):** CaptainsHat, CaptainsHatJedi (same nif),
  ArmyHelmet, ArmyHelmetPunk (same nif), GunslingerHat, CaptainsHood (airship) —
  M+F nifs. NOTE one nif can serve 2 ARMOs (→ one repositioned nif, a new ARMA
  per ARMO). **Full-head skips:** gasmask/captain hoods, VaultTec helmets.
- **Plugin template (from FurryFallout.esp):** furry ARMA listed FIRST in the
  ARMO addon list, all `INDX=0` (order = priority); races via RNAM + additional
  MODL. Plugin build needs a PluginSet with the FO4 master chain so cross-plugin
  FormID refs (FFO races) denormalize correctly.

## Goal
Make EAC head-slot gear sit correctly on the (taller) FFO furry head, and add a
plugin so furry races use the repositioned meshes. Vanilla/other races keep the
original mesh.

## Inputs / outputs
- **Source mod (EAC):** `…\Vortex\fallout4\mods\2.1.0 CBBE BODY AND
  BODYSLIDES-22431-2-1-0-1709115070\` → `Eli_Armour_Compendium - Main.BA2`,
  `… - Textures.BA2`, `Eli_Armour_Compendium.esp`.
- **Unpack to:** new folder under `C:\Modding\FalloutAssets\` (e.g.
  `…\EAC Assets\`). Extract with esplib `ba2.py` (`list_files`/`read_file`) →
  write loose. (Textures.BA2 only needed if we inspect materials; meshes are in
  Main.BA2.)
- **Patch output:** `C:\Users\hughr\Documents\FFO_EAC_Patch\` — repositioned
  nifs under `Meshes\FFO\…`, the patch `.esp`, and a CSV log.

## Reference measurement (verified)
Vanilla `Clothes\BaseballUniform\MHat.nif` vs FFO version:
- Both skinned to `HEAD`; `global_to_skin` + `skin_to_bone` IDENTICAL.
- Difference is in the **vertices** (skin space): dx≈0.00, dy≈−0.07, **dz≈+2.00**
  (±0.25 spread = vertex-compression noise). → a near-uniform skin-space
  translation, essentially **+2.0 on Z**.

## Two passes

- First pass identifies ONE headpart and executes the full process. 
- Hugh checks the output and validates in game.
- When Hugh confirms, run the process on the rest.

## Pipeline
1. **Extract** Main.BA2 → `…\EAC Assets\` (loose).
2. **Validate the offset** (do this before mass-processing): for several
   vanilla↔FFO head pairs that already exist (Baseball MHat, plus others FFO
   ships), measure the per-vertex delta. Confirm it's the same constant
   (≈ 0, −0.07, +2.0) everywhere. If it varies materially, revisit (see Q3). [HUgh: Skip this step. I chose the baseball cap because it is just a reposition. Other headgear has more complex modifications that I'm not asking you to try to duplicate.]
3. **Walk** every `.nif` under the extracted meshes. For each, classify:
   - **Headpart?** shape(s) are `BSSubIndexTriShape` skinned to the `HEAD` bone,
     FO4 segments only `{0 empty, 1 head}` (no body segments 2–6), with a `.ssf`.
   - **Full-head replacement?** (the exception — left alone). Detection TBD —
     see Q2. Candidate rules: the mesh encloses the head origin / spans the whole
     head bbox; or the ARMA biped slot is 32 (FaceGen head) / it carries a head
     "hair"/scalp segment; or vertex/extent heuristics. Will refine against the
     real EAC nifs and record the rule used.
   - **Not a headpart** → skip (body/hand/etc. armour; out of scope).
4. **Reposition** each in-scope headpart: add the offset to every vertex of each
   head-skinned shape, leave skin/segments/shaders untouched, save to
   `…\FFO_EAC_Patch\Meshes\FFO\<original-relative-path>`.
   (Verts-translate matches how FFO authored MHat; alternative would be shifting
   `global_to_skin`, but FFO baked it into verts.)
5. **Plugin patch** (`FFO_EAC_Patch.esp`, mastering `Eli_Armour_Compendium.esp`
   + `FurryFallout.esp`): for each EAC ARMO whose addon list points at a
   repositioned headpart, insert a **new ARMA** at the FRONT of the ARMO's
   addon (INDX/MODL) list:
   - new ARMA: male/female model = the new `Meshes\FFO\…` nif; primary race
     (RNAM) + Additional Races (MODL) = **all 20 FurryFallout.esp races** (10
     adult + 10 child).
   - existing vanilla ARMA stays after it → non-furry races fall through.
   - Reuse the FFO furrifier's normalize-FormID conventions (never bare objid).
6. **CSV log** (`…\FFO_EAC_Patch\eac_headgear.csv`): one row per nif examined —
   columns like: nif path, is_headpart (Y/N), classification (hat / full-head /
   not-head), action (repositioned / skipped-fullhead / skipped-nonhead),
   offset applied, output path, ARMO(s) patched, notes. [Hugh: Correlate this with the plugin ARMO/ARMA records as well.]
7. **Testing file** Create test plugin and batch file. The test plugin has a furry NPC wearing one of the headparts - one NPC per headpart. Rotate the NPC races. The batch file is called eac.txt and instantiates each NPC with "player.placeatme <formid>".

## Open questions / issues
- **Q1 — Offset value & space.** I measured ≈ (0, −0.07, +2.0) skin-space from
  MHat. Did you create the FFO heads with an exact, intended number (e.g. exactly
  +2.0 Z, dy=0)? I'd rather apply the intended constant than the noisy measured
  mean. Confirm the value, or I'll use the best-fit mean across several pairs.
  Answer:  I chose the baseball cap because it is just a reposition. Other headgear has more complex modifications that I'm not asking you to try to duplicate. All furry heads take the same repositioning, by design.
- **Q2 — "Replaces the whole head" detection.** What concretely distinguishes
  these from hats? (e.g. hoods/full helmets/masks that wrap the entire head.)
  Is it a biped slot (32 = FaceGen head?), the mesh enclosing the head, or do you
  just want me to flag candidates in the CSV for you to confirm rather than
  auto-skip? This is the fuzziest rule. 
  Answer: As far as I can tell, the only way is to look at the bodypart flags in the plugin. Since I asked for a csv, you'll be building a correlation between ARMO / AMRA / vanilla nif / furry nif as you go.
- **Q3 — Is the offset truly global?** Plan validates across a few pairs. If some
  EAC headgear is rigged differently (different `global_to_skin`/extra bones),
  the constant skin-space offset won't be right — handle per-nif or flag.
  Answer: Flag it. 
- **Q4 — Which FurryFallout.esp / load order?** Multiple copies exist (`Furry
  Fallout Assets`, `Furry_Fallout V3.9`, …). I used `Furry Fallout Assets`
  (20 races). Confirm that's the canonical/active one for the master + race list.
  Answer: That's the one.
- **Q5 — ARMA scope.** Patch only ARMOs whose addon mesh we actually
  repositioned (i.e. headgear we changed)? And only head-slot ARMOs — confirm we
  ignore body/hand armour entirely.
  Answer: Yes
- **Q6 — Where should the generator code live?** A standalone script under
  `furrifier_fo4/` (e.g. `eac_headgear/`)? It reuses esplib + PyNifly like the
  rest. (No game-launch needed.)
  Answer: Make it a standalone script. It's highly likely we'll use it again on other mods.

## Validation
- Re-dump a couple of output nifs: confirm verts shifted by the offset, segments/
  SSF/shaders/bones unchanged, file rooted at `Meshes\FFO\…`.
- Load the patch esp in esplib: confirm each target ARMO has the new ARMA first,
  the ARMA references the FFO nif + 20 races, masters correct.
- Spot-check one in NifSkope / in-game on a furry NPC.
