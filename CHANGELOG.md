# Release notes

## v1.2.0 — Mod Organizer support

First standalone release. Earlier versions shipped bundled inside the Furry Fallout kit,
so if you installed the kit, the version you already have is 1.1.1.

The furrifier didn't work under MO2. If you use MO2, including any Wabbajack modlist, this
is the release that makes the tool usable at all. Vortex users were never affected by the
bug and are unaffected by the fix.

### Fixed

- **Mod Organizer 2 support.** MO2 shows programs a merged view of game files plus your
  mods, but that view is only visible to some of the ways Windows can ask about a file.
  The furrifier was using one of the ways it *isn't* visible, so every plugin, mesh,
  texture and archive that came from a mod rather than from the base game read as
  "missing". *And* the plugin list read from plugins.txt, so it showed plugins that other
  parts of the code couldn't find.

- **FaceGen no longer discards work it has already done.** A diagnostic line that measured
  each finished mesh was failing under MO2, and taking the finished mesh down with it — a
  run in which every face baked correctly reported "0 succeeded, N failed."

- **The plugin list shows what's actually loadable.** It used to list everything named in
  plugins.txt whether the file could be read or not, so plugins you'd ticked came back
  "missing" mid-run. 

- **Textures the tool refused to read.** The uncompressed DX10 formats 87, 88, 91 and 93
  are decoded now. A texture that still can't be loaded now names the file.

- **A typo in a race catalog no longer kills the whole catalog.** 
  - A range written where a probability belongs is rejected with a message, instead of
  crashing the load 
  - `weight_range = [hi, lo]` is read as min-to-max 
  - A bad axis warns and is skipped while the other axes still load.

- **A patch name typed without `.esp` in the GUI** produced a file with no extension. The
  extension is now added if missing.

### New

- **`--data-dir` on the GUI** so a Mod Organizer executable entry can launch the furrifier
  already pointed at the right Data folder. For many Wabbajack lists that's
  `<modlist>\Stock Game\Data` — *not* your Steam install.

- `--resources` is accepted as a synonym for `--data-dir`

- **Changing the data directory reports how much of your load order is present**,
  immediately — "2 of 3 active plugins are not in \<dir\>" at the moment you choose the
  folder, rather than an empty patch a long while later.

- **Fur colour varies between NPCs of the same breed.** Every fur alpha in the shipped
  race catalog is a band now rather than one fixed value, so each NPC draws its own. See
  the note below.

- **`[lo, hi]` ranges allowed anywhere a number is expected**: face-morph position and
  rotation per axis, scale, morph weights, and colour intensity. E.g. `scale = [0.2, 0.6]`
  gives a race varied snouts instead of one cloned face; `position = [[-0.4, -0.1], 0, 0]`
  jitters X while pinning Y and Z. Each NPC's draw is deterministic and keyed per field,
  so two ranges on one face don't move in lockstep. Probabilities and breed weights
  don't take ranges — rolling against a random probability is the same
  distribution as rolling against its midpoint.

- **Headgear patching mirrors Furry Fallout's own meshes.** When a mod ships its own armor
  pointing at a vanilla helmet, the patcher now reuses FFO's race-specific furry versions
  (`_Cat`, `_Dog`, `_Horse`…) instead of nudging the human mesh onto a furry head, and
  carries the mod's own material swaps onto every variant. America Rising 2: 14 armors
  handled, up from 12 with 6 crude path-mapped reuses.

### Notes

- **MO2 users:** point the tool at the Data folder MO2 virtualizes, which for a Wabbajack
  list may be the modlist's own game copy, not the Steam install. Find this folder with
  MO's folder icon -> "Open Game folder". Output written into that folder is redirected to
  MO2's `overwrite\`, where "Create Mod from Overwrite" turns it into a mod.

- **Fur colours change for everyone.** Because the alphas are now bands, tint output
  may from a 1.1.1 run for every furrified NPC, even at the same scheme and seed.

- **Traditional semantic versioning.** Version numbers are `<major-version>.<minor-version>.<patch-version> build <build-number>`. 
  - Major version = important new functionality, behavior changes that are not backwards compatible.
  - Minor version = functionality tweaks and additions, bug fixes.
  - Patch version = Quick-turnaround bug fixes 
  - Build number = unique build number for internal use

## v1.1.1 — 2026-07-22

Bundled in the Furry Fallout kit; never released standalone.

- **Story NPCs are protected.** The player, Shaun, and both spouse presets
  are left alone, so a run can't overwrite the race the FFO-Player plugins
  set or mangle the intro and family scenes.
- **Synths get their own assignments.** Coursers and Gen3 synths route
  through per-race probabilities of their own.
- Headgear patcher rewritten.
