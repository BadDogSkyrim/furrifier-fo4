"""Standalone: build an FFO compatibility patch for a mod's HEADGEAR.

For every head-slot nif in a source mod (detected from its segments) that does
NOT replace the whole head, reposition it onto the FFO furry head by the fixed
offset measured from the Baseball-cap reference pair, save it under meshes/FFO/,
and emit a plugin that inserts a furry ARMA (the FFO mesh + every FurryFallout
race) ahead of the vanilla ARMA in each affected ARMO. Also emits a CSV log and
a test plugin (one furrified NPC per headpart, rotated races, + baked facegen).

Reusable: add an entry to MODS and run `python headgear_patch.py <tag>`. Needs
the source mod's meshes already extracted to disk (BA2 unpacked).
"""
import os, sys, struct, csv
sys.path.insert(0, r"C:\Modding\xEditDev\furrifier_fo4\src")
sys.path.insert(0, r"C:\Modding\xEditDev\esplib\src")
os.environ.setdefault("PYNIFLY_DEV_ROOT", r"C:\Modding")
sys.path.insert(0, r"C:\Modding\PyNifly\io_scene_nifly")
import logging; logging.disable(logging.CRITICAL)
from pathlib import Path
from esplib import LoadOrder, Plugin, FormID
from esplib.record import Record
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

    # --- find target ARMOs: addon ARMA whose nif is a headpart, slot != FaceGen ---
    targets = []          # (armo, head_arma, [(slot_tuple)], full_head)
    csv_rows = []
    repositioned = set()  # normalized vanilla rel paths we repositioned ourselves
    reused = {}           # rel paths served by a mesh FFO already ships
    for o in eac.get_records_by_signature("ARMO"):
        addons = [eac_arma.get(m.get_form_id().value) for m in o.get_subrecords("MODL")]
        addons = [a for a in addons if a]
        if not addons:
            continue
        head_arma = None
        for a in addons:
            m2 = modelof(a["MOD2"]) or modelof(a["MOD3"])
            if m2 and is_headpart_nif(find_mesh(norm(m2))):
                head_arma = a
                break
        if head_arma is None:
            continue
        sl = slots(head_arma["BOD2"])
        full = FACEGEN_HEAD_SLOT in sl
        nifs = [modelof(head_arma["MOD2"]), modelof(head_arma["MOD3"])]
        nifs = [norm(n) for n in nifs if n]
        if not full:
            for rel in nifs:
                if rel in repositioned or reused.get(rel):
                    continue
                if ffo_existing(rel):
                    reused[rel] = True
                    continue
                dst = os.path.join(OUT, "Meshes", "FFO", rel)
                reposition(find_mesh(rel), dst, off)
                repositioned.add(rel)
            targets.append((o, head_arma, sl, nifs))
        csv_rows.append({
            "ARMO": o.editor_id, "ARMO_formid": f"{o.form_id.value:08X}",
            "ARMA": head_arma.editor_id, "ARMA_formid": f"{head_arma.form_id.value:08X}",
            "BOD2_slots": " ".join(map(str, sl)),
            "full_head": "Y" if full else "N",
            "vanilla_nif_M": "meshes\\" + nifs[0] if nifs else "",
            "furry_nif_M": "" if full else ("meshes\\FFO\\" + nifs[0] if nifs else ""),
            "action": ("skip (full head)" if full else
                       "reused FFO mesh + ARMA patched"
                       if nifs and reused.get(nifs[0]) else
                       "repositioned + ARMA patched"),
        })
    print(f"targets: {len(targets)} ARMOs; repositioned {len(repositioned)} nifs, "
          f"reused {len(reused)} FFO meshes")

    # --- patch plugin ---
    patch = Plugin.new_plugin(os.path.join(OUT, PATCH_NAME), masters=[], game="fo4")
    patch.plugin_set = ps
    for o, arma, sl, nifs in targets:
        na = patch.copy_record(arma, eac, new_form_id=True)
        na.editor_id = (arma.editor_id or "AA") + "_FFO"
        for sig in ("MOD2", "MOD3"):
            sr = na.get_subrecord(sig)
            if sr and sr.get_string():
                sr.set_string("FFO\\" + sr.get_string())
        na.subrecords = [s for s in na.subrecords if s.signature != "MODL"]
        rnam = na.get_subrecord("RNAM") or na.add_subrecord("RNAM", b"\x00\x00\x00\x00")
        patch.write_form_id(rnam, 0, race_norm[0])
        for rn in race_norm[1:]:
            patch.write_form_id(na.add_subrecord("MODL", b"\x00\x00\x00\x00"), 0, rn)
        ov = patch.copy_record(o, eac)
        first = next((i for i, s in enumerate(ov.subrecords) if s.signature == "INDX"),
                     len(ov.subrecords))
        ov.insert_subrecord(first, "INDX", struct.pack("<H", 0))
        modl = ov.insert_subrecord(first + 1, "MODL", b"\x00\x00\x00\x00")
        patch.write_form_id(modl, 0, patch.normalize_form_id(na.form_id))
        # record new ARMA edid in the CSV row
        for row in csv_rows:
            if row["ARMO"] == o.editor_id:
                row["new_ARMA"] = na.editor_id
                row["races"] = len(races)
                row["offset"] = "(%.3f,%.3f,%.3f)" % off
    patch.add_recursive_masters(eac)
    patch.add_recursive_masters(ffo)
    patch.sort_masters()
    patch.save()
    print(f"saved {PATCH_NAME}; masters={len(patch.header.masters)}")

    # --- CSV ---
    cols = ["ARMO", "ARMO_formid", "ARMA", "ARMA_formid", "BOD2_slots", "full_head",
            "vanilla_nif_M", "furry_nif_M", "new_ARMA", "races", "offset", "action"]
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
    test = Plugin.new_plugin(os.path.join(OUT, TEST_NAME), masters=[], game="fo4")
    test.plugin_set = ps
    placeatme = []
    for i, (o, arma, sl, nifs) in enumerate(targets):
        race_edid = adult_races[i % len(adult_races)]
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
