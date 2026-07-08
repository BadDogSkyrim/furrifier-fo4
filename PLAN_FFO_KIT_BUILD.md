# PLAN: Furry Fallout (FFO) Kit / FOMOD Build Script

Goal: a build script — analogous to `Build_YAS_Reborn.bat` — that assembles the
Furry Fallout release kits from the currently-active Vortex "mods" (packing loose
assets into BA2s where required), authors/updates the FOMOD `ModuleConfig.xml`,
copies in the freshly-built furrifier, and archives **three** kits for release.

> **Status (2026-07-06):** blockers reviewed and answered; plan revised from
> disk-verified sources. All decisions captured under **DECISIONS**. World ESPs
> now sourced from the `_Working` folders (Hugh copied them in). Ready to build.

---

## Paths

| What | Path |
|---|---|
| Vortex mods root | `C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods` |
| SFW FOMOD (build target) | `C:\Users\hughr\OneDrive\Fallout4Dev\KitsFFO\Furry_Fallout` |
| NSFW FOMOD (build target) | `C:\Users\hughr\OneDrive\Fallout4Dev\KitsFFO\Furry_Fallout_NSFW` |
| Furrifier build output | `C:\Modding\xEditDev\furrifier_fo4\dist\furrify_fo4` |
| Reference script (Skyrim) | `C:\Users\hughr\OneDrive\SkyrimDev\Build_YAS_Reborn.bat` |
| Output kits | `C:\Users\hughr\OneDrive\Fallout4Dev\` (3 archives — see **Outputs**) |

---

## Three kits (Q6)

1. **SFW** — the main FOMOD. Packed + rebuilt from sources into `KitsFFO\Furry_Fallout`, then archived.
2. **NSFW** — a **separate** FOMOD at `KitsFFO\Furry_Fallout_NSFW` (loose assets, not packed).
3. **Prebuilt Facegen** — a plain `.zip` (no FOMOD) of the already-packed `FFO Working Facegen`.

---

## DECISIONS (Hugh)

- **D1.** `Furry_Fallout` (consolidated/packed), `Furry_Fallout (3.7)`, `Furry_Fallout V3.9`,
  `Furry_Fallout_NSFW V3.9`, `Furry Fallout Bugfix`, `Furry Fallout Pawfeet`, and all
  `*_NPC_2K *` mods are **OBSOLETE — do not use**. (NPC_2K mods are old facegen,
  superseded by `FFO Working Facegen`.)
- **D2.** Everything ships from a source **outside** the FOMOD target folder. (World
  ESPs `FurryFalloutWorld.esp` / `FurryFalloutWorldDLC.esp` now live in the respective
  `_World_Working` / `_DLC_World_Working` folders — Hugh copied them in.)
- **D3.** Primary assets + main/DLC/player ESPs all come from **`Furry Fallout Assets`**
  (verified: `FurryFallout.esp`, `FurryFalloutDLC.esp`, 10× `FFO-Player-*.esp`, plus
  loose `Materials/Meshes/Textures`). DLC assets ride in the base `FurryFallout` BA2.
- **D4.** Pawfeet outfits source is **`Furry Fallout Prebuilt Pawfeet`** (`FurryFalloutOutfits.esp`
  + `Meshes\FFO`), shipped **loose** (not packed).
- **D5.** Ship the **furrifier** (`dist\furrify_fo4`) as an **optional FOMOD step**
  (near Bodyslide), installed to `Data\Tools\Furrifier` (parallels `Data\Tools\BodySlide`).
- **D6.** The FOMOD's functional-folder layout is kept **but `ModuleConfig.xml` WILL be
  rewritten** — the new patch/furrifier/prebuilt options don't exist in the current XML.
- **Q1 (packing).** The **build packs** loose→BA2 (esplib `Ba2Writer`, validated in-game
  this session; BSArch/Archive2 as fallback).
- **Q4 (language).** **Python.**
- **Exclusions.** Anything starting with `xxx`, anything starting/ending with `TEST`
  (e.g. `FFO_EAC_Patch_TEST.esp`, `XXXTools\`), and junk (`*.log`, `*.bak`).

---

## Source inventory (verified on disk — Hugh's authored notes)

Most sources are loose files. Except where stated, loose files are packed into archives
to accompany their ESP.

### Main (SFW) FOMOD → `KitsFFO\Furry_Fallout`

- **`Furry Fallout Assets`** — loose `Materials\ Meshes\ Textures\` → **pack**. DLC assets
  fold into the `FurryFallout` archive (few, and inert without the DLC plugin).
- **`Furry Fallout Bodyslide`** — Bodyslide defs (`Tools\BodySlide\...`). Optional.
- **`Furry Fallout Prebuilt Bodies`** — prebuilt Bodytalk + CBBE bodies (`Meshes\FFO\Body`).
  **Loose**, optional.
- **`Furry Fallout Prebuilt Pawfeet`** — `FurryFalloutOutfits.esp` + `Meshes\FFO`. **Loose**.
  Pawfeet optional; if selected the plugin installs, with a **further** option for the
  prebuilt outfit meshes.
- **`Furry_Fallout_World_Working`** — vanilla furry world objects (loose). Optional.
- **`More_Furry_Posters (1)`**, **`More_Furry_Stuff`** — world objects (loose textures).
  Always included when "furry world" is selected.
- **`Furry_Fallout_DLC_World_Working`** — DLC furry world objects (loose). Optional.

**Patches** (own tab in the FOMOD UI):

- **`FFO_EAC_Patch`** — for Eli's Armour Compendium. Offered only if `Eli_Armour_Compendium.esp`
  present. `FFO_EAC_Patch.esp` + `meshes\ textures\` → **pack** (exclude `FFO_EAC_Patch_TEST.esp`).
- **`FFO WAT Patch Assets`** — for We Are The Minutemen. Offered only if `W.A.T.Minutemen.esp`
  present. **Loose** `Meshes\` only — **no ESP** (asset override).
- **`Furry_Fallout_SS2 (3.7)`** — for Sim Settlements 2. Offered only if `SS2.esm` present.
  `FFOSimSettlementPatch.esp` + `FFOSimSettlementWorldPatch.esp` + `- Main.ba2` (**already
  packed**, ship as-is).

### NSFW FOMOD → `KitsFFO\Furry_Fallout_NSFW`

- **`Furry Fallout NSFW Assets`** — `FFO_NSFW.esp`, `FFO_NSFW_LongJohns.esp`, `AAF\`,
  `Meshes\`. **Loose** (AAF assets ship loose). Separate FOMOD, authored fresh.

### Facegen kit (plain zip, no FOMOD)

- **`FFO Working Facegen`** — `FO4FurryPatch.esp` + `- Main.ba2` + `- Textures.ba2`
  (**already packed**). Zip these three; exclude `FO4FurryPatch.esp.bak`.

---

## Source → destination mapping

This is the authoritative definition of what goes where. The rest of the doc needs to be reconciled with this section - in particular the structure of the FOMOD has to make all these parts available. 

Bodyslide resources are moved to the NSFW mod because some of the bodies are nude, and anybody working with bodyslide has to be okay with nudity.

### SFW FOMOD

| Destination (in `Furry_Fallout\`) | Source | Action |
|---|---|---|
| `Data\FurryFallout - Main.ba2` + `- Textures.ba2` | `Furry Fallout Assets` (loose) | **pack** loose→BA2 (incl. DLC assets) |
| `Data\FurryFallout.esp`, `FurryFalloutDLC.esp` | `Furry Fallout Assets` | copy |
| `Data\FFO-Player-*.esp` (×10) | `Furry Fallout Assets` | copy |
| `World\FurryFalloutWorld - Main/Textures.ba2` | `Furry_Fallout_World_Working` + `More_Furry_Posters (1)` + `More_Furry_Stuff` | **pack** loose→BA2 |
| `World\FurryFalloutWorld.esp` | `Furry_Fallout_World_Working` | copy |
| `WorldDLC\FurryFalloutWorldDLC - Main/Textures.ba2` | `Furry_Fallout_DLC_World_Working` | **pack** loose→BA2 |
| `WorldDLC\FurryFalloutWorldDLC.esp` | `Furry_Fallout_DLC_World_Working` | copy |
| `Pawfeet\FurryFalloutOutfits.esp` | `Furry Fallout Prebuilt Pawfeet` | copy (loose) |
| `Pawfeet\Meshes\FFO\...` (prebuilt outfits) | `Furry Fallout Prebuilt Pawfeet\Meshes\FFO` | copy (loose, sub-option) |
| `Tools\Furrifier\...` | `dist\furrify_fo4` | copy (D5) |
| `Patches\EAC\...` (esp + BA2) | `FFO_EAC_Patch` (excl. `*_TEST.esp`) | **pack** assets→BA2, copy esp |
| `Patches\WAT\Meshes\...` | `FFO WAT Patch Assets` | copy (loose) |
| `Patches\SS2\...` (2 esp + BA2) | `Furry_Fallout_SS2 (3.7)` | copy (already packed) |
| `Images\`, `Fomod\` | existing FOMOD | static; `ModuleConfig.xml` **rewritten** (D6) |

> New functional folders introduced: `Tools\` (BodySlide + Furrifier), `Bodies\`,
> `Patches\` (EAC/WAT/SS2). Exact BA2 vs loose per the "Action" column.

### NSFW FOMOD

| Destination (in `Furry_Fallout_NSFW\`) | Source | Action |
|---|---|---|
| bodies | "Furry Fallout Prebuilt Bodies" | copy (do not pack) |
| world | "Furry Fallout NSFW Packable Assets" | copy the ESP files; pack the loose files; base the archives on "FFO_NSFW.esp" | 
| loose | "Furry Fallout NSFW Assets" | copy the loose files - do not pack  | 
| femmags | "Furry Fallout Female Magazines" | copy, do not pack |
| bodyslide | "Furry Fallout Bodyslide" | copy |
| `Fomod\ModuleConfig.xml`, `Images\` | authored fresh | do not touch |

### Facegen zip

`FO4FurryPatch.esp` + `FO4FurryPatch - Main.ba2` + `FO4FurryPatch - Textures.ba2`
(from `FFO Working Facegen`, exclude `.bak`) → `Furry_Fallout_Facegen.zip`.

---

## FOMOD design (`ModuleConfig.xml`)

The current SFW XML has: DLC toggle, Furry World / Human World, Pawfeet Outfits +
Bodyslide, race selection, and a conditional WorldDLC install. It must **grow** to
cover the new sources. Proposed steps/tabs:

1. **Basic Setup** 
   1. `Include DLCs` (SelectAtMostOne, flag `include_dlc`, req. all DLC masters); 
   2. `Furry World` / `Human World` (SelectExactlyOne, flag `furry_world`, packs in posters+stuff); 
   3. `Outfits` (SelectAny): *Pawfeet Outfits* → `FurryFalloutOutfits.esp`, *Prebuilt Outfit Meshes* 
2. **Player Race** — SelectExactlyOne over Human + 10 races (unchanged).
3. **Tools** — *Furrifier* (optional; installs `Tools\Furrifier`).
4. **Patches** (own tab) — EAC / WAT / SS2, each an option **auto-selected/visible only when
   its master is present** via `conditionalFileInstalls` + `fileDependency`:
   - EAC ← `Eli_Armour_Compendium.esp` active
   - WAT ← `W.A.T.Minutemen.esp` active
   - SS2 ← `SS2.esm` active
5. **conditionalFileInstalls** — existing WorldDLC (`include_dlc` AND `furry_world`) +
   the three patch patterns above.

NSFW FOMOD: 

1. Required install of 
2. **Bodies** installs the contents of the `bodies` folder. 
3. **Furry World** installs the `world/` and `loose/` folders, except `world\FFO_NSFW_LongJohns.esp`. Selected by default iff `FurryFalloutWorld.esp` is active in the load order.
4. **Female porn magazines** installs `femmags`. Default off.
5. **Long Johns** installs `world\FFO_NSFW_LongJohns.esp'. Default off.
6. **Bodyslide** installs the `bodyslide` folder.

---

## Outputs

- `C:\Users\hughr\OneDrive\Fallout4Dev\Furry_Fallout.7z` (SFW, `-mx=9`)
- `C:\Users\hughr\OneDrive\Fallout4Dev\Furry_Fallout_NSFW.7z` (NSFW, `-mx=9`)
- `C:\Users\hughr\OneDrive\Fallout4Dev\Furry_Fallout_Facegen.zip` (facegen)

---

## Script outline (Python)

1. Resolve paths; validate every source mod + both FOMOD targets exist; build the
   exclude matcher (`xxx*`, `*TEST*`, `*.log`, `*.bak`).
2. **Pack SFW BA2s** (esplib `Ba2Writer`):
   - `Furry Fallout Assets` → `Data\FurryFallout - Main.ba2` (GNRL) + `- Textures.ba2` (DX10).
   - `World_Working` + posters + stuff → `World\FurryFalloutWorld - *.ba2`.
   - `DLC_World_Working` → `WorldDLC\FurryFalloutWorldDLC - *.ba2`.
   - `FFO_EAC_Patch` assets → `Patches\EAC\...ba2`.
3. **Copy ESPs / loose sets** per the mapping table (player ESPs, world ESPs, outfits,
   bodyslide, prebuilt bodies/pawfeet, WAT loose, SS2 as-is).
4. **Copy furrifier** `dist\furrify_fo4` → `Tools\Furrifier`.
5. **Author/refresh `ModuleConfig.xml`** (SFW) with the new option/patch structure.
6. **Assemble NSFW FOMOD** (loose copy + fresh ModuleConfig).
7. **Prune junk** (`CreationKitPlatformExtended.log`, excluded patterns).
8. **Archive**: SFW `.7z`, NSFW `.7z`, facegen `.zip`.
9. Print summary + timing (like YAS).

---

## Notes

- Furrifier exe is rebuilt separately via `pyinstaller furrify_fo4.spec --noconfirm --clean`
  (RECIPES.md). The build script assumes `dist\furrify_fo4` is current (or runs it first).
- **TODO (content, separate):** rewrite the kit README as a proper user guide (Hugh).
- Related ship blocker (separate task): pawfeet dismemberment fix — see
  `[[project_ffo_pawfeet_dismember]]`.
