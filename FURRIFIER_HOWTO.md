# Furrifier — How-To

The **Furrifier** turns the NPCs of the Commonwealth furry and bakes their faces,
so the people around you match the furry world. It works on vanilla and DLC NPCs
**and** on NPCs added by other mods, and it's deterministic — the same NPC always
becomes the same furry race, so your game stays consistent run to run.

You run it once after setting up your load order. It writes a single patch plugin
plus the baked faces.

> **Do you even need this?** If you just want vanilla + DLC NPCs furried and don't use
> other NPC mods, you can install the `Prebuilt Facegen` download instead and skip the
> Furrifier entirely. Run the Furrifier when you want your *own* load order covered —
> including NPCs from other mods. Want to assign furry races according to **your own scheme**?
> **Got your own furry race** you want to incorporate? See below for instructions.

---

## Before you start

1. Install Furry Fallout (and any NPC mods you use) and get your **load order settled**.
   The Furrifier reads your load order; if you add NPC mods later, just run it again.
2. **Close the game.**
3. Find the tool in `Data\Tools\Furrifier` (or wherever your mod manager deployed it).

---

## Running it

Launch **`furrify_fo4_gui.exe`**. The window has a handful of fields; the defaults are
right for most people, so a normal run is really just *check the plugin list → Run*.

### The fields, top to bottom

- **Scheme** — Which furry races get handed out to which NPCs. Leave it on the default
  (`ffo_scheme`) unless you've created your own.

- **Patch file** — Name of the plugin the tool writes (default `FO4FurryPatch.esp`). No need to
  change it.

- **Data dir (read)** — Leave blank to auto-detect your Fallout 4 `Data` folder. Set it
  only if auto-detect picks the wrong one.

- **Output dir (write)** — Leave blank to write into your Data folder. Set it to stage the output somewhere else.

   - If you use only one profile and it's furry, it's fine to  generate directly into the game folder. 

   - If you switch between profiles with a different mix of mods in each, you may want to manage your NPCs as their own separate mod. In that case, generate into your mod's folder. 

- **Only factions** *(optional)* — restrict the run to NPCs in specific factions
  (comma-separated editor IDs). Handy for testing; but you can use it to implement simple schemes, e.g. make all raiders furry, or all Covenant furry.

- **Plugins** — Set the plugins to furrify. Defaults to your active load order, so it's usually fine as-is. 

- **Limit** *(optional)* — cap the number of NPCs processed. For testing a scheme.

- **Preview Pane** allows you to see how specific NPCs will be furrified. Look at an NPC by typing their editor ID. "Roll" allows you to view random NCPs.


### FaceGen options

- **Build FaceGen** — Leave **on**. This bakes each NPC's face; without it the NPCs get
  furry records but no baked face and the engine will have to generate them at run time. You might turn this off if you're testing out a new furry race and don't want to take the time for facegen.

- **Size** — Face texture resolution. **1024** is the sweet spot. Higher (2048/4096) is
  sharper but much larger on disk and harder on the engine; 512/256 are for low-end machines.

- **Throttle** — Bake with a single low-priority worker so your PC stays usable while it
  runs - but it will run slower. Off = use all cores (faster, may pin the machine).

- **Pack BA2** — After baking, bundle the faces into `FO4FurryPatch - Main.ba2` +
  `- Textures.ba2` and remove the loose files. **Recommended** — a clean install with no
  thousands of loose files for your mod manager to track. Takes an extra minute or two.

### Behavior options

- **Re-furrify already-furry NPCs** — On, it regenerates NPCs a previous run
  already did (from their vanilla base). There's no harm in turning it on, but you only need it if  if you changed your scheme and - want everyone redone.

- **Diversify clone-army faces** — Bethesda got very lazy with generic NPCs and many classes only have a few different faces, especially if their heads are hidden by helmets or masks (e.g. the DC guards). This option creates more template faces for these classes. Leave on for variety; off will be faster and smaller, and will fit into a Light (ESL) plugin (see below).

- **Light (ESL) plugin** — Flag the patch light so it doesn't use a load-order slot
  (the `.esp` extension stays). If a run makes more than ~2048 new records it can't be
  light and is saved as a full ESP automatically. You'll get a warning but the run will be fine. To fit as light on a big run, turn `Diversify clone-army faces` off.

### Go

Click **Run**. Progress shows at the bottom; a big load order takes a while (this is
mostly the face baking — use **Throttle** if you want to keep working).

---

## After the run

1. The tool writes the patch plugin (`FO4FurryPatch.esp` by default) and, unless you
   turned off **Pack BA2**, its two archives — into your output/Data folder.
2. If you generated faces in a mod, deploy (Vortex) or enable (MO) the mod.
3. Enable the patch in your mod manager and put it **below Furry Fallout** in the load
   order.
4. Launch the game — the NPCs are now furry.

## Running it again

Re-run any time you change your load order (new NPC mods, etc.). With **Re-furrify** off,
a new run only touches NPCs it hasn't done yet, so it's quick. Changing your **Scheme**
and want everyone re-rolled? Turn **Re-furrify** on for that run.

---

## Troubleshooting

- **"It saved as a full ESP even though I asked for Light."** The run minted too many
  records. Turn off **Diversify clone-army faces** and run again.
- **The machine is unusable while it bakes.** Turn on **Throttle**.

- **An NPC still looks human.** Confirm the patch is enabled and loads *below* Furry
  Fallout, and that the NPC's plugin was in the **Plugins** list when you ran.

- **Faces look low-res.** Bump **Size** to 2048 and re-run with **Re-furrify** on.

## Advanced

There's a command-line version (`furrify_fo4.exe`) with the same options as flags — run
`furrify_fo4.exe --help`. Custom schemes live in `schemes\<name>.toml` next to the exe;
pick one with the **Scheme** dropdown (or `--scheme`).

## New Schemes

Maybe you don't like the distribution of races that you get by default. Maybe you want all raiders to be hyenas, or you want the entire institute human in an otherwise furry world. You can do that by setting up your own scheme. 

1. Create a copy of `Data\furrifier_fo4\schemes\ffo_scheme.toml`, giving it whatever name you like. (But keep the ".toml" extension and put it in the same folder.)

2. `exclude_headparts` is a list of specialty hair that should not be assigned by default. Leave it alone, unless there's weird hair in your mod list.

3. Each `class_probabilities` section defines the relative weights for furry races in a class. Any race not listed will not be assigned to that class.

4. `npc_assignments` lets you force specific NPCs to a race. By default there's a companion of each race, plus a few extra by personal preference.

### Classes

You can create a new class. Follow the examples in `Data\furrifier_fo4\builtin.toml`. The format is 

```
class_match = [ [<class-name>, <RACE | FACTION | EDITORID>, <race-editorid>], ... ]
```

This would, for example, allow you to furrify all Covenant to one race by using the `CovenantFaction` faction.

### Families

It's jarring for members of the same family to be all different races. You can specify that NPCs are part of the same family. They will all be given the race of the first family member listed.

```
families = [ [<npc-editorid], ...], ... ]
```

### Aliases

Sometimes several NPC records represent a single NPC in game, for technical reasons. You can tell the furrifier these are all the same person and should be given identical appearance.

```
aliases = [ [<npc-editor-id>, ...], ... ]
```

## New Races

If you want to incorporate a new furry race, you need a scheme that assigns that race to classes, as above. 

You also need a race definition file. Createa a copy of `Data\furrifier_fo4\races\ffo_races.toml`, naming it for your race (e.g. `ffo_vulpine.toml`).

The one defition you *need* in that file is:

```
[[race_customization]]
race = "VulpineRace" # or whatever the editor ID actually is
```

That's it. You're done. The race will be used according to your scheme with random race-specific headparts and tints.

However, that can produce terrible-looking faces, depending on the complexity of the race. So you can tune the look of your race by limiting options. Read the `ffo_races` file for the details, but you have the ability to:

- Associate a **child race** with the main race (it doesn't have to have been designed as the race's child race - e.g. you could use the FFOFoxChildRace as children for Vulpines).
- Define **`breeds`** - a specification of a restricted range of appearance options. E.g. `FFODeerRace` is one race, but `WhiteTailBreed`, `AntelopeBreed`, etc. restrict options so the NPC looks coherent.
- Define **weight ranges** for your race (no fat cheetahs).
- Define **color schemes** - which tint layers to use and which colors to use for each layer.
- Define **face morphs** - which face morph options to use
- Define **headparts** - which headparts are valid on this breed or race.

**Note on tint layers**: FO4 allows you to define the blend mode for a tint layer but CK seems to ignore it entirely. The furrifer facegen does *not* - it honors whatever blend mode you've defined. This allows you to get a true white or dark, no matter the underlying color, or a soft mix of shade where that's desired. Use the preview in the furrifier to see what you'll get.

**single_race.toml** is a scheme that assigns every NPC to a single race. Useful when you're fine-tuning a race and you just want to see how it came out on a bunch of different NPCs.