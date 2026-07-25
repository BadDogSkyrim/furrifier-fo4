"""Standalone: build an FFO compatibility patch for a mod's HEADGEAR.

Two ways a mod's head-slot armor gets furrified, tried in that order:

1. **Vanilla re-use.** Mods routinely ship their own ARMO/ARMA pointing at a
   *vanilla* nif. FurryFallout already furrified that nif -- typically as
   several ARMAs, each with a race-specific mesh (`_Cat`, `_Dog`, `_Horse`, ...)
   covering a slice of the furry races. We mirror it exactly: clone the MOD's
   ARMA (keeping its customizations -- slots, priorities, material swaps) once
   per FFO variant and graft FFO's model + race list on. Hand-fitted meshes
   beat our uniform nudge, and it costs no new geometry.
2. **Reposition.** For a mod's *own* head mesh, shift it onto the taller furry
   head by the fixed offset measured from the Baseball-cap reference pair, save
   under meshes/FFO/, and mint one ARMA covering every FurryFallout race.

Either way the new ARMAs are inserted ahead of the mod's own ARMA in an ARMO
override, so non-furry races fall through to it unchanged. Also emits a CSV log
and a test plugin (one furrified NPC per headpart, rotated races, + facegen).

Reusable: add an entry to MODS and run `python headgear_patch.py <tag>`. Needs
the source mod's meshes already extracted to disk (BA2 unpacked).
"""
import os, sys, struct, csv, collections
sys.path.insert(0, r"C:\Modding\xEditDev\furrifier_fo4\src")
sys.path.insert(0, r"C:\Modding\xEditDev\esplib\src")
os.environ.setdefault("PYNIFLY_DEV_ROOT", r"C:\Modding")
sys.path.insert(0, r"C:\Modding\PyNifly\io_scene_nifly")
import logging; logging.disable(logging.CRITICAL)
from pathlib import Path
from esplib import LoadOrder, Plugin, FormID, AbsoluteFormID
from esplib.record import Record, SubRecord
from pyn.pynifly import NifFile
from furrifier_fo4.world import FurryWorld
from furrifier_fo4.furrify import apply_furry
from furrifier_fo4.facegen import build_facegen_for_patch
from furrifier_fo4.models import Sex

# ---------------- CONFIG ----------------
VANILLA_DIR = r"C:\Modding\FalloutAssets\00 FO4 Assets"
FFO_ASSETS = r"C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Furry Fallout Assets"
FFO_ESP = "FurryFallout.esp"
OUT = r"C:\Users\hughr\AppData\Roaming\Vortex\fallout4\mods\Sandbox"
VANILLA_REF = os.path.join(VANILLA_DIR, r"Meshes\Clothes\BaseballUniform\MHat.nif")
FURRY_REF = os.path.join(FFO_ASSETS, r"Meshes\FFO\Clothes\BaseballUniform\MHat.nif")
SCHEME = "ffo_scheme"
FACEGEN_HEAD_SLOT = 32                                 # BOD2 bit -> "replaces whole head"

# Per source mod. `assets` are searched in order for a nif referenced by an
# ARMA; list the mod's own extracted meshes first, vanilla last (mods routinely
# re-use vanilla headgear meshes under their own ARMA/ARMO records).
MODS = {
    "eac": dict(
        tag="eac",
        esp="Eli_Armour_Compendium.esp",
        assets=[r"C:\Modding\FalloutAssets\EAC Assets", VANILLA_DIR],
    ),
    "ar2": dict(
        tag="ar2",
        esp="AmericaRising2.esm",
        assets=[r"C:\Modding\FalloutAssets\America Rising 2", VANILLA_DIR],
    ),
}
MOD = MODS[sys.argv[1] if len(sys.argv) > 1 else "eac"]
MOD_ESP = MOD["esp"]
TAG = MOD["tag"]
PATCH_NAME = f"FFO_{TAG.upper()}_Patch.esp"
TEST_NAME = f"FFO_{TAG.upper()}_Patch_TEST.esp"


def norm(p):
    p = (p or "").replace("/", "\\").lower().lstrip("\\")
    return p[7:] if p.startswith("meshes\\") else p


def find_mesh(rel):
    """Absolute path of a nif (relative to meshes\\) in the mod's asset roots."""
    for root in MOD["assets"]:
        p = os.path.join(root, "meshes", rel)
        if os.path.exists(p):
            return p
    return os.path.join(MOD["assets"][0], "meshes", rel)


def ffo_existing(rel):
    """Absolute path of an already-shipped FFO version of `rel`, or None.

    FFO ships furry-fitted copies of the vanilla headgear it covers; when a mod
    re-uses one of those vanilla meshes we point the patched ARMA at FFO's copy
    instead of repositioning our own duplicate.
    """
    p = os.path.join(FFO_ASSETS, "Meshes", "FFO", rel)
    return p if os.path.exists(p) else None


# The ARMA biped-model blocks, in schema order (defs/fo4.py: EspGroup 'Male
# Biped Model' / 'Female Biped Model'). Order matters -- these are parsed as a
# group, so a stray sig out of sequence re-parses as a second group instance and
# serializes back scrambled.
MALE_MODEL = ("MOD2", "MO2T", "MO2C", "MO2S", "MO2F")
FEMALE_MODEL = ("MOD3", "MO3T", "MO3C", "MO3S", "MO3F")
# ...of which the material swaps stay with the MOD when we graft FFO's mesh on:
# they're the mod's paint job, they still apply to FFO's re-fit of the same
# model, and they're exactly the customization we clone the record to keep.
# Everything else in the block describes the mesh, so it travels with the mesh.
KEEP_FROM_MOD = ("MO2S", "MO3S")

# One furry treatment FFO gave a vanilla ARMA: the ARMO it came from, the
# vanilla ARMA it furrifies, and FFO's ARMA records (one per race group).
VariantSet = collections.namedtuple("VariantSet", "armo vanilla armas")

# The same variant sets keyed two ways -- see build_furry_index.
FurryIndex = collections.namedtuple("FurryIndex", "by_mesh by_arma")

# One planned action on one addon of a mod ARMO. kind is:
#   "reuse"      -- the mod's own ARMA re-uses a vanilla nif: clone it per FFO
#                   variant and graft FFO's mesh + races on.
#   "insert"     -- the ARMO lists a VANILLA ARMA directly: FFO's furry ARMAs
#                   for it already exist, so just list them. `arma` is None.
#   "reposition" -- the mod's own head mesh: nudge `nifs` onto the furry head.
Step = collections.namedtuple("Step", "arma kind variants nifs slots")


def races_of(arma):
    """Every race an ARMA serves -- RNAM plus the trailing MODLs -- absolute."""
    out = []
    rn = arma.get_subrecord("RNAM")
    if rn:
        out.append(arma.normalize_form_id(rn.get_form_id()))
    return out + [arma.normalize_form_id(m.get_form_id())
                  for m in arma.get_subrecords("MODL")]


def build_furry_index(ps, ffo):
    """Index the furry ARMAs FFO built for vanilla armor, two ways.

    FFO furrifies a vanilla ARMO by inserting furry ARMAs -- each with its own
    race-specific mesh and a slice of the furry race list -- ahead of the
    vanilla ARMA. Diffing FFO's addon list against the base record yields
    exactly those; pairing each to the vanilla ARMA it furrifies by EditorID
    (`FFO_<vanilla edid>[_Suffix]`, which holds for every one of them) gives a
    variant SET per vanilla ARMA. Returns a FurryIndex keyed:

    - `by_mesh`  -- the vanilla ARMA's mesh paths, for a mod that ships its own
      ARMA pointing at a vanilla nif (clone it, graft FFO's mesh + races on).
    - `by_arma`  -- the vanilla ARMA's own FormID, for a mod whose ARMO lists
      the vanilla ARMA *directly*. Nothing to clone there, and FFO's ARMAs
      already exist, so those just get listed -- no new records at all.

    Each value is a LIST of variant sets, not a flat ARMA list: a few vanilla
    ARMAs are furrified twice over (the army helmet and its `_postwar` twin, the
    shortsleeves glasses), and applying both sets would list ARMAs with
    overlapping race coverage. See pick_best.
    """
    nmasters = len(ffo.header.masters)
    by_mesh = collections.defaultdict(list)
    by_arma = collections.defaultdict(list)
    for o in ffo.get_records_by_signature("ARMO"):
        if (o.form_id.value >> 24) >= nmasters:
            continue                    # an FFO-original ARMO, not an override
        chain = ps.get_override_chain(o.normalize_form_id(o.form_id).value)
        base = chain[0] if chain else None
        if base is None or base is o:
            continue
        ffo_ids = addons_of(o)
        base_ids = addons_of(base)
        added = [f for f in ffo_ids if f not in base_ids]
        if not added:
            continue
        # The vanilla ARMAs FFO kept -- the furry ones are named after them.
        van = {}
        for f in ffo_ids:
            if f in base_ids:
                r = ps.resolve_form_id(AbsoluteFormID(f))
                if r is not None and r.editor_id:
                    van[r.editor_id] = r
        per_van = collections.defaultdict(list)
        for f in added:
            r = ps.resolve_form_id(AbsoluteFormID(f))
            if r is None:
                continue
            edid = r.editor_id or ""
            # Longest vanilla edid first, so `AAHelmetArmy_postwar` wins over
            # `AAHelmetArmy` when both are addons of the same ARMO.
            hit = next((v for v in sorted(van, key=len, reverse=True)
                        if edid.startswith("FFO_" + v) or edid.startswith("FFO" + v)),
                       None)
            if hit is None and len(van) == 1:
                hit = next(iter(van))   # unambiguous even without the naming
            if hit is not None:
                per_van[hit].append(r)
        for ve, armas in per_van.items():
            vr = van[ve]
            vs = VariantSet(o.editor_id, vr, tuple(armas))
            key = vr.normalize_form_id(vr.form_id).value
            if vs not in by_arma[key]:
                by_arma[key].append(vs)
            for vp in (modelof(vr["MOD2"]), modelof(vr["MOD3"])):
                if vp and vs not in by_mesh[norm(vp)]:
                    by_mesh[norm(vp)].append(vs)
    return FurryIndex(by_mesh, by_arma)


def pick_best(sets):
    """(VariantSet, ambiguous) from the candidates for one key, or (None, False).

    More than one set means FFO furrified the same vanilla armor twice over;
    take the one covering the most races and tie-break on the vanilla ARMA's
    EditorID so the choice is stable across runs. `ambiguous` surfaces it in the
    CSV rather than letting the guess pass silently.
    """
    if not sets:
        return None, False
    best = max(sets, key=lambda s: (
        len({r.value for a in s.armas for r in races_of(a)}),
        s.vanilla.editor_id or ""))
    return best, len(sets) > 1


def pick_for_mesh(index, arma):
    """The FFO variant set for an ARMA pointing at a vanilla mesh, or None."""
    for p in (modelof(arma["MOD2"]), modelof(arma["MOD3"])):
        sets = index.by_mesh.get(norm(p))
        if sets:
            return pick_best(sets)
    return None, False


def variant_suffix(ffo_arma, vanilla_arma, i):
    """`FFO_AA_Gasmask_Dog` + vanilla `AA_Gasmask` -> `_Dog`. FFO names a sole
    variant after the vanilla ARMA with no suffix at all, hence the fallback."""
    e, v = ffo_arma.editor_id or "", vanilla_arma.editor_id or ""
    for pre in ("FFO_" + v, "FFO" + v):
        if e.startswith(pre):
            return e[len(pre):] or f"_{i}"
    return f"_{i}"


def mint_furry_arma(patch, mod_arma, src, ffo_arma, suffix):
    """Clone the mod's ARMA and graft FFO's mesh + races onto the copy.

    Cloning is the point: the mod's own BOD2 slots, DNAM priorities, material
    swaps and keywords survive, so its armor keeps looking like its armor.
    """
    na = patch.copy_record(mod_arma, src, new_form_id=True)
    na.editor_id = (mod_arma.editor_id or "AA") + "_FFO" + suffix
    graft_model(na, ffo_arma, MALE_MODEL)
    graft_model(na, ffo_arma, FEMALE_MODEL)
    set_races(patch, na, races_of(ffo_arma))
    return na


def graft_model(clone, ffo_arma, sigs):
    """Swap one biped-model block on `clone` for FFO's, keeping the mod's own
    material swap, and splice the result back where the block was.

    Rebuilt as a whole block in schema order rather than edited piecemeal: the
    block is an EspGroup, so leaving a leftover sig ahead of its MOD2/MOD3 makes
    esplib re-parse it as a second group instance and write it back scrambled.
    """
    keep = {s.signature: s for s in clone.subrecords
            if s.signature in KEEP_FROM_MOD}
    at = next((i for i, s in enumerate(clone.subrecords) if s.signature in sigs),
              len(clone.subrecords))
    block = []
    for sig in sigs:
        src = keep.get(sig) if sig in KEEP_FROM_MOD else ffo_arma.get_subrecord(sig)
        if src is not None:
            block.append(SubRecord(sig, bytes(src.data)))
    # Nothing before `at` is a model sig, so `at` still points at the block's
    # start once the old one is filtered out.
    clone.subrecords = [s for s in clone.subrecords if s.signature not in sigs]
    clone.subrecords[at:at] = block
    clone.modified = True


def set_races(patch, arma, race_ids):
    """Rewrite an ARMA's race list: RNAM is the first, trailing MODLs the rest."""
    arma.remove_subrecords("MODL")
    rnam = arma.get_subrecord("RNAM") or arma.add_subrecord("RNAM", b"\x00\x00\x00\x00")
    patch.write_form_id(rnam, 0, race_ids[0])
    for r in race_ids[1:]:
        patch.write_form_id(arma.add_subrecord("MODL", b"\x00\x00\x00\x00"), 0, r)


def addons_of(armo):
    """The ARMA FormIDs an ARMO lists, as absolute values."""
    return [armo.normalize_form_id(m.get_form_id()).value
            for m in armo.get_subrecords("MODL")]


def prepend_addons(patch, armo_override, addon_ids):
    """Insert INDX/MODL pairs for `addon_ids` (absolute) ahead of the existing
    addon list, so furry races match first and everyone else falls through to
    the ARMA already there. Mirrors how FFO patches a vanilla ARMO (INDX = 0)."""
    # Two addons of one ARMO can resolve to the same FFO variant set, so drop
    # anything already listed rather than listing an ARMA twice.
    seen = set(addons_of(armo_override))
    at = next((i for i, s in enumerate(armo_override.subrecords)
               if s.signature == "INDX"), len(armo_override.subrecords))
    i = 0
    for fid in addon_ids:
        if fid.value in seen:
            continue
        seen.add(fid.value)
        armo_override.insert_subrecord(at + 2 * i, "INDX", struct.pack("<H", 0))
        modl = armo_override.insert_subrecord(at + 2 * i + 1, "MODL", b"\x00\x00\x00\x00")
        patch.write_form_id(modl, 0, fid)
        i += 1


def slots(bod2):
    v = bod2["first_person_flags"] if isinstance(bod2, dict) else (bod2 or 0)
    return tuple(30 + i for i in range(32) if v & (1 << i))


def modelof(v):
    return "" if v is None else (v if isinstance(v, str) else (v.get("model", "") or ""))


def head_offset():
    a = NifFile(VANILLA_REF).shapes[0].verts
    b = NifFile(FURRY_REF).shapes[0].verts
    n = len(a)
    return tuple(sum(b[i][k] - a[i][k] for i in range(n)) / n for k in range(3))


def is_headpart_nif(path):
    """True if any shape is a head-slot piece. Two accepted shapes: a segmented
    BSSubIndexTriShape (.ssf, segments only {0,1}) skinned to HEAD, or a shape
    skinned to NOTHING BUT head bones — some mods ship hats as a plain
    BSTriShape weighted to HEAD alone, with no segment file at all. EAC bundles
    goggles sub-shapes (segs 0-3), so this is per-shape."""
    try:
        nif = NifFile(path)
    except Exception:
        return False
    for sh in nif.shapes:
        bones = list(sh.bone_names or [])
        if not bones:
            continue
        if all(b.upper().startswith("HEAD") for b in bones):
            return True
        if sh.blockname != "BSSubIndexTriShape":
            continue
        segs = [s.index for s in sh.partitions if type(s).__name__ == "FO4Segment"]
        try:
            ssf = sh.segment_file or ""
        except Exception:
            ssf = ""
        if ssf and segs and max(segs) <= 1 and any(
                b.upper().startswith("HEAD") for b in bones):
            return True
    return False


def _rot_vec(rot, v):
    return tuple(rot[r][0]*v[0] + rot[r][1]*v[1] + rot[r][2]*v[2] for r in range(3))


def reposition(in_path, out_path, offset):
    nif = NifFile(in_path)
    for sh in nif.shapes:
        bones = list(sh.bone_names) if _has_bones(sh) else []
        if bones:
            for b in bones:
                stb = sh.get_shape_skin_to_bone(b)
                if stb is None:
                    continue
                d = _rot_vec(stb.rotation, offset)
                for k in range(3):
                    stb.translation[k] += d[k]
                sh.set_skin_to_bone_xform(b, stb)
        else:
            t = sh.transform
            for k in range(3):
                t.translation[k] += offset[k]
            sh.transform = t
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    nif.filepath = out_path
    nif.save()


def _has_bones(sh):
    try:
        return bool(list(sh.bone_names))
    except Exception:
        return False


def main():
    off = head_offset()
    print("head offset:", tuple(round(x, 4) for x in off))

    print("building FurryWorld…")
    plugins = list(LoadOrder.from_game("fo4", active_only=True))
    world = FurryWorld(SCHEME, plugins=plugins)
    ps = world.ps
    eac = ps.get_plugin(MOD_ESP)
    print(f"source mod: {MOD_ESP}")
    ffo = ps.get_plugin(FFO_ESP)
    f4 = ps.get_plugin("Fallout4.esm")

    races = [r for r in ffo.get_records_by_signature("RACE") if r.editor_id]
    race_norm = [r.normalize_form_id(r.form_id) for r in races]
    adult_races = [r.editor_id for r in races if "child" not in r.editor_id.lower()]
    print(f"{len(races)} races ({len(adult_races)} adult)")

    eac_arma = {a.form_id.value: a for a in eac.get_records_by_signature("ARMA")}

    print("indexing FFO's vanilla-armor patches…")
    furry_index = build_furry_index(ps, ffo)
    print(f"  {len(furry_index.by_mesh)} vanilla mesh paths / "
          f"{len(furry_index.by_arma)} vanilla ARMAs FFO already furrifies")

    # --- plan, per mod ARMO: what to do with each of its addons (see Step) ---
    targets = []          # (armo, [Step, ...])
    csv_rows = []
    repositioned = set()  # normalized rel paths we repositioned ourselves
    reused = {}           # rel paths served by a mesh FFO already ships
    reuse_meshes = set()  # vanilla rel paths FFO furrifies via its own ARMAs
    for o in eac.get_records_by_signature("ARMO"):
        plan = []
        for m in o.get_subrecords("MODL"):
            a = eac_arma.get(m.get_form_id().value)
            if a is None:
                # Not the mod's own ARMA -- the ARMO lists a vanilla one
                # directly. If FFO furrified that exact record its furry ARMAs
                # already exist, so list them: no clone, no new records.
                fid = o.normalize_form_id(m.get_form_id())
                vs, ambiguous = pick_best(furry_index.by_arma.get(fid.value))
                if vs is None:
                    continue
                plan.append(Step(None, "insert", vs, [], ()))
                csv_rows.append({
                    "ARMO": o.editor_id, "ARMO_formid": f"{o.form_id.value:08X}",
                    "ARMA": vs.vanilla.editor_id,
                    "ARMA_formid": f"{fid.value:08X}",
                    "BOD2_slots": " ".join(map(str, slots(vs.vanilla["BOD2"]))),
                    "full_head": "N",
                    "method": "FFO ARMA listed (vanilla addon)",
                    "vanilla_nif_M": modelof(vs.vanilla["MOD2"]),
                    "furry_nif_M": "; ".join(modelof(f["MOD2"]) for f in vs.armas),
                    "new_ARMA": ", ".join(f.editor_id for f in vs.armas),
                    "races": len({r.value for f in vs.armas for r in races_of(f)}),
                    "note": (f"FFO's own ARMAs, reused as-is (from {vs.armo})"
                             + (" [AMBIGUOUS: >1 FFO treatment, took widest]"
                                if ambiguous else "")),
                })
                continue
            sl = slots(a["BOD2"])
            nifs = [norm(n) for n in (modelof(a["MOD2"]), modelof(a["MOD3"])) if n]
            row = {
                "ARMO": o.editor_id, "ARMO_formid": f"{o.form_id.value:08X}",
                "ARMA": a.editor_id, "ARMA_formid": f"{a.form_id.value:08X}",
                "BOD2_slots": " ".join(map(str, sl)),
                "full_head": "Y" if FACEGEN_HEAD_SLOT in sl else "N",
                "vanilla_nif_M": "meshes\\" + nifs[0] if nifs else "",
            }
            # 1. vanilla re-use -- FFO's own race-specific meshes beat our nudge,
            #    and this applies to any slot, not just headgear.
            vs, ambiguous = pick_for_mesh(furry_index, a)
            if vs is not None:
                plan.append(Step(a, "reuse", vs, nifs, sl))
                reuse_meshes.update(nifs)
                row.update(
                    method="FFO vanilla re-use",
                    furry_nif_M="; ".join(modelof(f["MOD2"]) for f in vs.armas),
                    races=len({r.value for f in vs.armas for r in races_of(f)}),
                    note=(f"as FFO furrifies {vs.vanilla.editor_id} in {vs.armo}"
                          + (" [AMBIGUOUS: >1 FFO treatment, took widest]"
                             if ambiguous else "")))
                csv_rows.append(row)
                continue
            # 2. the mod's own head mesh -- reposition, unless it replaces the
            #    whole head (FaceGen owns that; a nudged copy would fight it).
            if not any(is_headpart_nif(find_mesh(n)) for n in nifs):
                continue
            if FACEGEN_HEAD_SLOT in sl:
                row.update(method="", action="skip (full head)")
                csv_rows.append(row)
                continue
            for rel in nifs:
                if rel in repositioned or reused.get(rel):
                    continue
                if ffo_existing(rel):
                    reused[rel] = True
                    continue
                reposition(find_mesh(rel), os.path.join(OUT, "Meshes", "FFO", rel), off)
                repositioned.add(rel)
            plan.append(Step(a, "reposition", None, nifs, sl))
            row.update(
                method=("reused FFO mesh" if nifs and reused.get(nifs[0])
                        else "repositioned"),
                furry_nif_M="meshes\\FFO\\" + nifs[0] if nifs else "",
                races=len(races), offset="(%.3f,%.3f,%.3f)" % off)
            csv_rows.append(row)
        if plan:
            targets.append((o, plan))
    kinds = collections.Counter(s.kind for _, p in targets for s in p)
    print(f"targets: {len(targets)} ARMOs;  "
          f"{kinds['reuse']} ARMA(s) re-use a vanilla mesh ({len(reuse_meshes)} meshes), "
          f"{kinds['insert']} vanilla addon(s) served by FFO's own ARMAs, "
          f"{kinds['reposition']} repositioned "
          f"({len(repositioned)} nifs, {len(reused)} FFO meshes reused)")

    # --- patch plugin ---
    patch = Plugin.new_plugin(os.path.join(OUT, PATCH_NAME), masters=[], game="fo4")
    patch.plugin_set = ps
    n_minted = 0
    for o, plan in targets:
        addon_ids, minted = [], []
        for st in plan:
            if st.kind == "insert":
                # FFO's ARMAs already exist and already carry the right meshes
                # and races -- listing them is the whole patch.
                addon_ids += [f.normalize_form_id(f.form_id) for f in st.variants.armas]
                continue
            if st.kind == "reuse":
                for i, fa in enumerate(st.variants.armas):
                    na = mint_furry_arma(patch, st.arma, eac, fa,
                                         variant_suffix(fa, st.variants.vanilla, i))
                    addon_ids.append(patch.normalize_form_id(na.form_id))
                    minted.append(na.editor_id)
                continue
            na = patch.copy_record(st.arma, eac, new_form_id=True)
            na.editor_id = (st.arma.editor_id or "AA") + "_FFO"
            for sig in ("MOD2", "MOD3"):
                sr = na.get_subrecord(sig)
                if sr and sr.get_string():
                    sr.set_string("FFO\\" + sr.get_string())
            set_races(patch, na, race_norm)
            addon_ids.append(patch.normalize_form_id(na.form_id))
            minted.append(na.editor_id)
        prepend_addons(patch, patch.copy_record(o, eac), addon_ids)
        n_minted += len(minted)
        for row in csv_rows:
            if row["ARMO"] == o.editor_id and not row.get("new_ARMA"):
                row["new_ARMA"] = ", ".join(minted)
    patch.add_recursive_masters(eac)
    patch.add_recursive_masters(ffo)
    patch.sort_masters()
    patch.save()
    print(f"saved {PATCH_NAME}; masters={len(patch.header.masters)}, "
          f"{n_minted} new ARMAs minted")

    # --- CSV ---
    cols = ["ARMO", "ARMO_formid", "ARMA", "ARMA_formid", "BOD2_slots", "full_head",
            "method", "vanilla_nif_M", "furry_nif_M", "new_ARMA", "races", "offset",
            "note", "action"]
    with open(os.path.join(OUT, f"{TAG}_headgear.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(csv_rows, key=lambda r: (r["full_head"], r["ARMO"])):
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"wrote {TAG}_headgear.csv")

    # --- test plugin: one furrified NPC per target ARMO, rotated races ---
    def find_base(female):
        human = f4.get_record_by_editor_id("HumanRace")
        human = human.normalize_form_id(human.form_id).value
        for npc in f4.get_records_by_signature("NPC_"):
            rn = npc.get_subrecord("RNAM")
            a = npc.get_subrecord("ACBS")
            if not (rn and a and len(a.data) >= 16):
                continue
            if npc.normalize_form_id(rn.get_form_id()).value != human:
                continue
            if struct.unpack_from("<H", a.data, 14)[0] & 1:        # use-traits
                continue
            is_f = bool(struct.unpack_from("<I", a.data, 0)[0] & 0x1)  # ACBS Female flag
            if is_f != female or not npc.editor_id:
                continue
            if npc.get_subrecord("FTST") or npc.get_subrecord("NAM9"):
                return npc
        return None

    base_m, base_f = find_base(False), find_base(True)
    def covered_races(plan):
        """The adult races this ARMO's furry ARMAs actually serve.

        A variant set need not span every race -- FFO furrifies the raider hood
        for Horse and Deer only -- so a flat rotation can hand a test NPC a race
        its armor has no furry ARMA for. That NPC then wears the plain human
        mesh and the test looks like it passed. Rotate within the covered set
        instead. The reposition path mints one ARMA over every race, so it
        imposes no restriction.
        """
        edids = set()
        for st in plan:
            if st.kind == "reposition":
                return adult_races
            for fa in st.variants.armas:
                for r in races_of(fa):
                    rr = ps.resolve_form_id(r)
                    if rr is not None and rr.editor_id:
                        edids.add(rr.editor_id)
        return [e for e in adult_races if e in edids] or adult_races

    test = Plugin.new_plugin(os.path.join(OUT, TEST_NAME), masters=[], game="fo4")
    test.plugin_set = ps
    placeatme = []
    for i, (o, plan) in enumerate(targets):
        pool = covered_races(plan)
        race_edid = pool[i % len(pool)]
        furry_race = world.races.resolve(race_edid, False)
        female = bool(i % 2)
        base = base_f if female else base_m
        # outfit -> this ARMO
        otft = Record("OTFT", FormID(0), 0); otft.plugin = test
        otft.add_subrecord("EDID").set_string(f"{TAG.upper()}_Test_{o.editor_id}_OTFT")
        inam = otft.add_subrecord("INAM", b"\x00\x00\x00\x00")
        test.add_record(otft)
        test.write_form_id(inam, 0, o.normalize_form_id(o.form_id))
        npc = test.copy_record(base, f4, new_form_id=True)
        npc.editor_id = f"{TAG.upper()}_Test_{o.editor_id}_{race_edid.replace('FFO','').replace('Race','')}"
        apply_furry(test, npc, furry_race, race_edid=race_edid,
                    sex=Sex.FEMALE if female else Sex.MALE, signature=npc.editor_id,
                    headpart_pools=world.headpart_pools, race_tints=world.race_tints,
                    customization=world.cust, race_morphs=world.race_morphs,
                    breed_signature=npc.editor_id)
        doft = npc.get_subrecord("DOFT") or npc.add_subrecord("DOFT", b"\x00\x00\x00\x00")
        test.write_form_id(doft, 0, test.normalize_form_id(otft.form_id))
        placeatme.append((npc.editor_id, npc.form_id.value & 0xFFFFFF))
    test.add_recursive_masters(eac)
    test.sort_masters()
    test.save()
    print(f"saved {TEST_NAME}: {len(placeatme)} test NPCs")

    fg = build_facegen_for_patch(
        test, ps, str(world.data),
        fallback_dir=str(world.fallback) if world.fallback else None,
        output_dir=OUT, extractor=world.extractor, templates=world.tint_templates,
        pools=world.headpart_pools, races_by_edid=world.races_by_edid,
        resolver=world.resolver, base_heads=world.base_heads,
        race_morphs=world.race_morphs, bone_regions=world.bone_regions)
    print("facegen:", fg)

    with open(os.path.join(OUT, f"{TAG}.txt"), "w") as fh:
        fh.write(f"; Replace XX with {TEST_NAME}'s load-order index.\n")
        for edid, obj in placeatme:
            fh.write(f"player.placeatme XX{obj:06X} 1\n")
    print(f"wrote {TAG}.txt")


if __name__ == "__main__":
    main()
