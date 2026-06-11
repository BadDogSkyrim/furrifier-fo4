"""Scheme model + the class-distribution engine (the FO4-specific core).

A `Scheme` holds everything a furrification run needs to decide *which* furry
race a given NPC becomes:

  - class_match rules (ordered, first-match-wins) sort NPCs into classes
  - class_probabilities distribute furry races across each class by weight
  - aliases collapse several records into one signature
  - families force shared race from the first member
  - npc_assignments pin a specific NPC (or family-leader) to a race

Race *resolution* is deterministic: a weighted pick keyed on the NPC's
signature hash, so every run yields the same assignment (PLAN_FO4_SCHEME.md).
The engine here is pure (no esplib record access) so it is fully unit-testable;
the caller supplies each NPC's facts via NpcFacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import (
    ClassDistribution, FURRIFIABLE_RACES, MatchRule, WeightedRace,
)
from .util import hash_string, wildcard_match


def _rule_matches(rule: MatchRule, facts: NpcFacts) -> bool:
    """True if a class-match rule matches the NPC's facts.

    RACE/EDITORID/NAME match the corresponding single field; FACTION
    matches if ANY of the NPC's factions matches the pattern. NAME is
    skipped when the NPC has no resolved FULL name.
    """
    f = rule.field
    if f == 'race':
        return wildcard_match(rule.pattern, facts.race)
    if f == 'editorid':
        return wildcard_match(rule.pattern, facts.editor_id)
    if f == 'name':
        return (facts.name is not None
                and wildcard_match(rule.pattern, facts.name))
    if f == 'faction':
        return any(wildcard_match(rule.pattern, fac) for fac in facts.factions)
    return False


# Seed for the class-distribution weighted pick. Distinct from any
# headpart/tint seed so race choice is decorrelated from appearance.
_RACE_SEED = 6151


@dataclass
class NpcFacts:
    """The facts about one NPC the classifier tests against.

    `signature` is the NPC's EditorID unless an alias remaps it. `factions`
    is the set of faction EditorIDs the NPC belongs to. All comparisons are
    case-insensitive (handled by the matchers).
    """
    signature: str
    editor_id: str
    race: str
    name: Optional[str] = None
    factions: frozenset = frozenset()


@dataclass
class Scheme:
    """Parsed furrification scheme + race-distribution data."""
    # Ordered class-match rules. Scheme-defined rules are prepended to the
    # built-ins (PLAN_FO4_SCHEME.md line 124), so earlier = higher priority.
    class_rules: list[MatchRule] = field(default_factory=list)
    # class_name -> distribution
    distributions: dict[str, ClassDistribution] = field(default_factory=dict)
    # alias signature (first entry) -> all member editor_ids (incl. first)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    # member editor_id -> alias signature (reverse index)
    _alias_of: dict[str, str] = field(default_factory=dict)
    # ordered family member lists (first member is the leader)
    families: list[list[str]] = field(default_factory=list)
    # member editor_id -> family leader editor_id
    _family_leader: dict[str, str] = field(default_factory=dict)
    # editor_id -> race (explicit per-NPC assignment)
    npc_assignments: dict[str, str] = field(default_factory=dict)
    # HDPT EditorIDs (lowercased) the scheme never wants applied — e.g. rad or
    # magazine hair. Handed to HeadpartPools, which filters them at pick time.
    exclude_headparts: set[str] = field(default_factory=set)


    def build_indexes(self) -> None:
        """Populate reverse lookups after the dataclass fields are filled.

        Idempotent. Keys are lowercased so EditorID comparisons are
        case-insensitive throughout.
        """
        self._alias_of = {}
        for sig, members in self.aliases.items():
            for m in members:
                self._alias_of[m.lower()] = sig
        self._family_leader = {}
        for fam in self.families:
            if not fam:
                continue
            leader = fam[0]
            for m in fam:
                self._family_leader[m.lower()] = leader


    # -- signature / classification ----------------------------------------

    def signature_for(self, editor_id: str) -> str:
        """The APPEARANCE hashing signature for an NPC: its alias's first entry
        if the NPC is part of an alias, else its own EditorID. Records sharing an
        alias hash identically — they ARE one NPC. Family members keep DISTINCT
        appearance signatures (relatives, not clones — distinct headparts/tints)."""
        return self._alias_of.get(editor_id.lower(), editor_id)


    def breed_signature_for(self, editor_id: str) -> str:
        """The signature for traits a family/alias SHARES — race AND breed. A
        family member (leader included) maps to the family leader; otherwise the
        alias signature (or own EditorID). So a family shares one breed (Riley &
        Kyle are both the same Deer breed) while still varying their headparts/
        tints via `signature_for`. The race is already shared via the leader
        recursion in `resolve_race`; this gives the breed roll the same key."""
        fam = self._family_leader.get(editor_id.lower())
        if fam is not None:
            return fam
        return self.signature_for(editor_id)


    def classify(self, facts: NpcFacts) -> Optional[str]:
        """Return the class name for an NPC, or None if no rule matches.

        Ordered first-match-wins. Only the candidate-gate races reach here;
        callers must gate on FURRIFIABLE_RACES before classifying.
        """
        for rule in self.class_rules:
            if _rule_matches(rule, facts):
                return rule.class_name
        return None


    # -- race resolution ---------------------------------------------------

    def resolve_race(self, facts: NpcFacts,
                     facts_lookup=None) -> Optional[str]:
        """Resolve the furry race for one NPC via the full precedence ladder:

          1. explicit assignment — by EditorID, then by alias signature
          2. family — a non-leader resolves exactly as its leader does
          3. class distribution — deterministic weighted pick

        `facts_lookup(editor_id) -> NpcFacts | None` resolves another NPC's
        facts, needed only to follow a family leader. The session layer
        passes a real lookup; unit tests pass a dict-backed one (or omit it
        when no families are involved).

        Returns None when the NPC isn't furrifiable (race outside the gate)
        or its class has no distribution. A returned 'HumanRace'/'GhoulRace'
        means "matched but deliberately left human" — the caller skips it.
        """
        if facts.race not in FURRIFIABLE_RACES:
            return None

        sig = self.signature_for(facts.editor_id)

        # 1. Explicit assignment. Checked against the NPC's own EditorID
        #    first (lets one record break away), then its alias signature
        #    so assigning an alias's first entry covers every member.
        direct = self.npc_assignments.get(facts.editor_id)
        if direct is None and sig != facts.editor_id:
            direct = self.npc_assignments.get(sig)
        if direct is not None:
            return direct

        # 2. Follow the leader. Alias members and family non-leaders both
        #    resolve EXACTLY as their leader does — same class, same race —
        #    so records that are one NPC (alias) or relatives (family) never
        #    diverge just because they classify differently on their own
        #    facts (e.g. a "...AsRaider" alias member that would otherwise
        #    land in CLASS_RAIDER). An alias member's leader is the alias's
        #    first entry (== its signature); a family non-leader's leader is
        #    the family head. We recurse on the leader's real facts; if the
        #    leader isn't in the load order we fall through and classify on
        #    our own facts — still hashed on the shared signature, so other
        #    aliased members stay consistent with each other.
        leader = None
        if sig.lower() != facts.editor_id.lower():
            leader = sig  # alias first entry
        else:
            fam = self._family_leader.get(facts.editor_id.lower())
            if fam is not None and fam.lower() != facts.editor_id.lower():
                leader = fam
        if leader is not None:
            leader_facts = facts_lookup(leader) if facts_lookup else None
            if leader_facts is not None:
                return self.resolve_race(leader_facts, facts_lookup)
            # leader absent from load order: fall through to classify below

        # 3. Class distribution, hashed on the (possibly alias-shared)
        #    signature so aliased members resolve identically.
        cls = self.classify(facts)
        if cls is None:
            return None
        return self._roll(cls, sig)


    def _roll(self, class_name: str, signature: str) -> Optional[str]:
        """Deterministic weighted pick across a class's race targets."""
        dist = self.distributions.get(class_name)
        if dist is None or dist.total_weight <= 0:
            return None
        roll = hash_string(signature, _RACE_SEED, dist.total_weight)
        cumulative = 0
        for wr in dist.races:
            cumulative += wr.weight
            if roll < cumulative:
                return wr.race
        return dist.races[-1].race  # numeric guard; not normally reached
