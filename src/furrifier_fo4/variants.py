"""Variant expansion: diversify clone-army templated NPCs.

A handful of trait templates back most of FO4's NPCs — one `EncRaider01Template`
behind ~290 raider leaves, two `EncSecurityDiamondCity` templates behind every
Diamond City guard. The memoize pass furrifies each trait *owner* ONCE, so every
raider / guard shows the same furry face. For heavily-used templates this module
replaces that single furrification with K appearance **variants**:

  - mint K furrified copies on distinct signatures — each rolls its own species +
    markings (standard furrification),
  - collect them in a fresh `LVLN` (leveled NPC list),
  - point an INJECTION NODE's `TPTA[Traits]` slot at that LVLN (Use-Traits flag).

The engine then rolls the LVLN per actor-instance at spawn, so the clone army
becomes a varied crowd — WITHOUT touching the leveled lists that distribute the
NPCs, and with level scaling intact (only the Traits slot is redirected).

**Where the redirect goes matters.** The engine honors a `TPTA[Traits]` redirect
on a template reached by a DIRECT link, but NOT on an actor it selected from a
leveled list. Redirecting the deep trait-owner therefore fails whenever that
owner sits behind a leveled list (e.g. DC guards: leaf -> LvlSec -> LChar(LVLN)
-> M01/M02 -> redirect ignored -> falls back to the Template00 cheetah). So we
inject at the leaf's CLOSEST traits template instead (`templates.
traits_injection_node`) — one direct hop the engine follows. See
PLAN_FO4_VARIANT_EXPANSION.md.

We only inject where a node has too few distinct faces already: a chain that
naturally resolves to >= SUFFICIENT_FACES owners is left alone (vanilla is
already varied there). K scales with the instance count, capped. The tunables
below are the only knobs; they are intentionally not exposed as config.
"""

from __future__ import annotations

import logging
import struct

from .templates import (is_templated_leaf, resolve_trait_owners,
                        traits_injection_node)

log = logging.getLogger(__name__)

# -- Tunables (internal; adjust here, no user-facing config) ----------------
EXPAND_THRESHOLD = 3       # min placed instances before a node is diversified
_K_FACTOR = 1.3            # birthday-paradox headroom over the instance count
_K_MIN = 8
_K_MAX = 24
# A chain that already resolves to this many distinct trait-owners is varied
# enough; we only inject when fewer faces are available than this floor.
SUFFICIENT_FACES = _K_MIN

# -- FO4 record layout constants --------------------------------------------
_USE_TRAITS = 0x0001
_ACBS_TEMPLATE_FLAGS_OFFSET = 14   # u16 template_flags within ACBS
_TPTA_SIZE = 52                    # 13 FormIDs; Traits is slot 0 (byte offset 0)
_LVLF_ALL_LEVELS = 0x01            # "calculate from all levels <= player's level"


def variant_count(instances: int) -> int:
    """K for an owner seen `instances` times: clamp(round(1.3*instances), 8, 24)."""
    return max(_K_MIN, min(_K_MAX, round(_K_FACTOR * instances)))


class InjectionPlan:
    """Where and how to inject variety for one injection node."""
    __slots__ = ("node", "instances", "faces", "variant_base", "k")

    def __init__(self, node, instances, faces, variant_base, k):
        self.node = node                  # object_index whose TPTA[Traits] we set
        self.instances = instances        # placed actors funneling through it
        self.faces = faces                # distinct trait-owner object_indexes below
        self.variant_base = variant_base  # owner object_index to copy for variants
        self.k = k                        # number of variants to mint


def plan_injections(plugin_set, winning_npc: dict, winning_lvln: dict) -> dict:
    """Decide which injection nodes to diversify, from the placed-actor scan.

    Walks every placed ACHR whose base is a templated leaf, attributing it to the
    leaf's injection node (`traits_injection_node` — the closest direct-link
    template) and recording the distinct trait-owners reachable below it. A node
    is planned when enough actors funnel through it (>= EXPAND_THRESHOLD) but it
    offers too few distinct faces (< SUFFICIENT_FACES) — i.e. a clone army the
    chain won't naturally vary. Non-templated/direct placements are not injected
    (furrified in place as before). Returns {node_obj: InjectionPlan}.
    """
    inst: dict = {}            # node -> instance count
    faces: dict = {}           # node -> set(owner objs)
    resolved: dict = {}        # base_obj -> (node, frozenset(owners)) | None

    def resolve(base_obj: int):
        if base_obj in resolved:
            return resolved[base_obj]
        base = winning_npc.get(base_obj)
        out = None
        if base is not None and is_templated_leaf(base):
            node = traits_injection_node(base, base_obj, winning_npc, winning_lvln)
            if node is not None:
                owners = resolve_trait_owners(base, winning_npc, winning_lvln)
                out = (node, frozenset(owners))
        resolved[base_obj] = out
        return out

    for plugin in plugin_set:
        for achr in plugin.get_records_by_signature('ACHR'):
            name = achr.get_subrecord('NAME')
            if name is None or len(name.data) < 4:
                continue
            base_obj = int.from_bytes(name.data[:4], 'little') & 0xFFFFFF
            hit = resolve(base_obj)
            if hit is None:
                continue
            node, owners = hit
            inst[node] = inst.get(node, 0) + 1
            faces.setdefault(node, set()).update(owners)

    plans: dict = {}
    for node, n in inst.items():
        fc = faces.get(node, set())
        # Need >=1 real owner to copy as the variant base; skip dead-end chains.
        if n >= EXPAND_THRESHOLD and 0 < len(fc) < SUFFICIENT_FACES:
            plans[node] = InjectionPlan(node, n, fc, min(fc), variant_count(n))
    return plans


class ExpansionResult:
    __slots__ = ("node_override", "lvln", "variants")

    def __init__(self, node_override, lvln, variants):
        self.node_override = node_override
        self.lvln = lvln
        self.variants = variants


def expand_at_node(patch, inject_node, variant_base, k: int, furrify_variant):
    """Inject up to `k` appearance variants at `inject_node`.

    `inject_node` is the record whose `TPTA[Traits]` slot is redirected (the
    leaf's closest direct-link template); `variant_base` is the trait-owner whose
    record is copied to mint variants (only its Traits are read downstream, so
    any owner with the right class works). `furrify_variant(record, signature) ->
    bool` resolves a race on `signature` and applies the furry appearance in
    place, returning False if that roll gated non-furry (the variant is dropped).

    Mints the furrified variants (fresh FormIDs, distinct EDIDs/signatures ->
    distinct species), builds an LVLN of them, and overrides `inject_node` to
    defer its Traits to the LVLN (Use-Traits flag + TPTA[Traits] slot only; every
    other template category and its TPLT are left intact). Returns an
    `ExpansionResult`, or None if no variant furrified (caller falls back).
    """
    node_edid = (inject_node.editor_id
                 or f"{inject_node.form_id.value & 0xFFFFFF:06X}")
    variants = []
    for i in range(k):
        v = patch.copy_record(variant_base, variant_base.plugin, new_form_id=True)
        v.editor_id = f"{node_edid}_F{i:02d}"
        if furrify_variant(v, v.editor_id):
            variants.append(v)
        else:
            patch.remove_record(v)   # this roll landed non-furry; drop it
    if not variants:
        return None

    lvln = _build_variant_lvln(patch, node_edid, variants)
    node_ov = patch.copy_record(inject_node, inject_node.plugin)  # override
    _redirect_traits_to_lvln(patch, node_ov, lvln)
    return ExpansionResult(node_ov, lvln, variants)


def _build_variant_lvln(patch, base_edid: str, variants: list):
    """A leveled-NPC list of `variants`, all at level 1 / count 1 so the engine
    picks one uniformly per spawn. Subrecords are added in canonical order
    (LVLN is unschematized, so insertion order is preserved)."""
    lvln = patch.new_record('LVLN', edid=f"{base_edid}_FurryVariants")
    lvln.add_subrecord('OBND', bytes(12))
    lvln.add_subrecord('LVLD', bytes(1))                 # chance none = 0
    lvln.add_subrecord('LVLM', bytes(1))                 # max count = 0
    lvln.add_subrecord('LVLF', bytes([_LVLF_ALL_LEVELS]))
    lvln.add_subrecord('LLCT', bytes([len(variants)]))
    for v in variants:
        entry = bytearray(12)
        struct.pack_into('<I', entry, 0, 1)              # level
        struct.pack_into('<I', entry, 8, 1)              # count
        lvlo = lvln.add_subrecord('LVLO', bytes(entry))
        patch.write_form_id(lvlo, 4, v.form_id)          # reference (sentinel+fixup)
    return lvln


def _redirect_traits_to_lvln(patch, node_ov, lvln) -> None:
    """On the injection-node override: set the Use-Traits flag and point the TPTA
    Traits slot (slot 0) at `lvln`, so the engine takes race+appearance from a
    rolled variant. Every other template category and the node's own TPLT are
    left intact — only the Traits slot is rewritten."""
    acbs = node_ov.get_subrecord('ACBS')
    if acbs is not None and len(acbs.data) >= _ACBS_TEMPLATE_FLAGS_OFFSET + 2:
        d = bytearray(acbs.data)
        flags = struct.unpack_from('<H', d, _ACBS_TEMPLATE_FLAGS_OFFSET)[0]
        struct.pack_into('<H', d, _ACBS_TEMPLATE_FLAGS_OFFSET, flags | _USE_TRAITS)
        acbs.data = d
        acbs.modified = True

    tpta = node_ov.get_subrecord('TPTA')
    if tpta is None:
        tpta = node_ov.add_subrecord('TPTA', bytes(_TPTA_SIZE))
    elif len(tpta.data) < _TPTA_SIZE:
        tpta.data = bytearray(tpta.data) + bytearray(_TPTA_SIZE - len(tpta.data))
    patch.write_form_id(tpta, 0, lvln.form_id)           # Traits slot
