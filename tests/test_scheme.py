"""Unit tests for the FO4 class-distribution engine (scheme.py / loader.py).

These are pure: no esplib/game files. They pin the behaviors PLAN_FO4_SCHEME.md
specifies — classifier ordering, the candidate gate, deterministic weighted
distribution, alias collapse, family follow, and the precedence ladder.
"""

import tomllib

import pytest

from furrifier_fo4.loader import (
    parse_scheme, SchemeError, load_scheme, _lint_top_level, _SCHEME_KEYS)
from furrifier_fo4.models import MatchRule, ClassDistribution, WeightedRace
from furrifier_fo4.scheme import Scheme, NpcFacts


def make_scheme(builtin="", scheme=""):
    return parse_scheme(tomllib.loads(builtin), tomllib.loads(scheme))


def test_lint_warns_on_unknown_top_level_key(caplog):
    # A [[facemorphs]] section misplaced in a scheme (it belongs in a race
    # catalog) must warn loudly, not silently drop.
    with caplog.at_level('WARNING'):
        _lint_top_level({'class_probabilities': [], 'facemorphs': {}},
                        _SCHEME_KEYS, 'single_race.toml')
    msgs = [r.message for r in caplog.records]
    assert any('unrecognized top-level key' in m and 'facemorphs' in m
               and 'single_race.toml' in m for m in msgs)
    # the recognized key isn't itself flagged (it appears only in the
    # "expected one of …" hint, never as "key '…'")
    assert not any("key 'class_probabilities'" in m for m in msgs)


def test_shipped_schemes_have_no_stray_top_level_keys(caplog):
    from furrifier_fo4.loader import list_available_schemes
    with caplog.at_level('WARNING'):
        for name in list_available_schemes():
            load_scheme(name)
    assert not any('unrecognized top-level key' in r.message
                   for r in caplog.records)


BUILTIN = """
class_match = [
    ["CLASS_GHOUL", "RACE", "GhoulRace"],
    ["CLASS_MINUTEMEN", "FACTION", "MinutemenFaction"],
    ["CLASS_MINUTEMEN", "EDITORID", "*Minutemen*"],
    ["CLASS_SETTLER", "NAME", "Settler"],
    ["CLASS_SETTLER", "RACE", "HumanRace"],
    ["CLASS_SETTLER", "RACE", "HumanChildRace"],
]
aliases = [
    ["Kellogg", "MQ203MemoryA_Kellogg", "DN088_Kellogg", "MQ101Kellogg"],
]
families = [
    ["JackCabot", "EmogeneCabotOld", "LorenzoCabot"],
]
"""

SCHEME = """
[[class_probabilities]]
class = "CLASS_MINUTEMEN"
races = [["FFOLykaiosRace", 30], ["FFOFoxRace", 15], ["HumanRace", 5]]

[[class_probabilities]]
class = "CLASS_SETTLER"
races = [["FFOFoxRace", 100]]

[[class_probabilities]]
class = "CLASS_GHOUL"
races = [["FFOGhoulDogRace", 100]]

[npc_assignments]
"JackCabot" = "FFOCheetahRace"
"""


# ---------------------------------------------------------------------------
# Loader / parsing
# ---------------------------------------------------------------------------


class TestLoader:


    def test_parses_all_sections(self):
        s = make_scheme(BUILTIN, SCHEME)
        assert len(s.class_rules) == 6
        assert 'Kellogg' in s.aliases
        assert s.families[0][0] == 'JackCabot'
        assert s.distributions['CLASS_SETTLER'].total_weight == 100
        assert s.npc_assignments['JackCabot'] == 'FFOCheetahRace'


    def test_exclude_headparts_parses_lowercased(self):
        s = make_scheme(BUILTIN, '''
exclude_headparts = ["RadHair01", "HairMagazine02"]
[[class_probabilities]]
class = "CLASS_SETTLER"
races = [["FFOFoxRace", 100]]
''')
        assert s.exclude_headparts == {'radhair01', 'hairmagazine02'}


    def test_exclude_headparts_default_empty(self):
        s = make_scheme(BUILTIN, SCHEME)
        assert s.exclude_headparts == set()


    def test_exclude_headparts_is_an_allowed_top_level_key(self, caplog):
        with caplog.at_level('WARNING'):
            _lint_top_level({'exclude_headparts': []}, _SCHEME_KEYS, 's.toml')
        assert not any('exclude_headparts' in r.message
                       for r in caplog.records)


    def test_scheme_classes_prepended(self):
        scheme = '''
        class_match = [["CLASS_FARHARBOR", "FACTION", "FarHarborFaction"]]
        [[class_probabilities]]
        class = "CLASS_FARHARBOR"
        races = [["FFOOtterRace", 100]]
        '''
        s = make_scheme(BUILTIN, scheme)
        # Scheme rule must come first (higher priority).
        assert s.class_rules[0].class_name == 'CLASS_FARHARBOR'


    def test_scheme_can_add_aliases(self):
        # A scheme adds an alias for a mod NPC; the built-in alias survives too.
        # (Top-level arrays must precede SCHEME's [npc_assignments] table.)
        scheme = 'aliases = [["ModNPC", "ModNPCMemoryVariant"]]\n' + SCHEME
        s = make_scheme(BUILTIN, scheme)
        assert 'Kellogg' in s.aliases          # built-in kept
        assert s.aliases['ModNPC'] == ['ModNPC', 'ModNPCMemoryVariant']
        # reverse index picks up the scheme member
        assert s._alias_of['modnpcmemoryvariant'] == 'ModNPC'


    def test_scheme_can_add_families(self):
        # A scheme adds a mod family; the built-in family survives too.
        scheme = 'families = [["ModLeader", "ModSibling"]]\n' + SCHEME
        s = make_scheme(BUILTIN, scheme)
        leaders = {fam[0] for fam in s.families}
        assert 'JackCabot' in leaders          # built-in kept
        assert 'ModLeader' in leaders
        assert s._family_leader['modsibling'] == 'ModLeader'


    def test_scheme_alias_overrides_builtin_signature(self):
        # Same signature in both: the scheme's member list wins.
        scheme = 'aliases = [["Kellogg", "SomeModKelloggClone"]]\n' + SCHEME
        s = make_scheme(BUILTIN, scheme)
        assert s.aliases['Kellogg'] == ['Kellogg', 'SomeModKelloggClone']


    def test_aliases_and_families_are_allowed_scheme_keys(self, caplog):
        with caplog.at_level('WARNING'):
            _lint_top_level({'aliases': [], 'families': []},
                            _SCHEME_KEYS, 's.toml')
        assert not any('unrecognized top-level key' in r.message
                       for r in caplog.records)


    def test_bad_scheme_alias_row_raises(self):
        with pytest.raises(SchemeError):
            make_scheme('', 'aliases = [["LoneEntry"]]')


    def test_bad_scheme_family_row_raises(self):
        with pytest.raises(SchemeError):
            make_scheme('', 'families = ["not-a-list"]')


    def test_bad_match_row_raises(self):
        with pytest.raises(SchemeError):
            make_scheme('class_match = [["X", "RACE"]]', '')


    def test_unknown_field_raises(self):
        with pytest.raises(SchemeError):
            make_scheme('class_match = [["X", "BOGUS", "y"]]', '')


    def test_zero_weight_class_raises(self):
        bad = '''
        [[class_probabilities]]
        class = "CLASS_X"
        races = [["FooRace", 0]]
        '''
        with pytest.raises(SchemeError):
            make_scheme('', bad)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassify:


    def setup_method(self):
        self.s = make_scheme(BUILTIN, SCHEME)


    def test_ghoul_wins_first(self):
        f = NpcFacts(signature='G', editor_id='G', race='GhoulRace',
                     factions=frozenset({'MinutemenFaction'}))
        # Even though it's a Minuteman, ghoul rule is first.
        assert self.s.classify(f) == 'CLASS_GHOUL'


    def test_faction_match(self):
        f = NpcFacts(signature='P', editor_id='Preston', race='HumanRace',
                     factions=frozenset({'MinutemenFaction'}))
        assert self.s.classify(f) == 'CLASS_MINUTEMEN'


    def test_editorid_wildcard(self):
        f = NpcFacts(signature='x', editor_id='SomeMinutemenGuard',
                     race='HumanRace')
        assert self.s.classify(f) == 'CLASS_MINUTEMEN'


    def test_name_match(self):
        f = NpcFacts(signature='s', editor_id='Generic01', race='HumanRace',
                     name='Settler')
        assert self.s.classify(f) == 'CLASS_SETTLER'


    def test_human_default(self):
        f = NpcFacts(signature='h', editor_id='RandomHuman', race='HumanRace')
        assert self.s.classify(f) == 'CLASS_SETTLER'


    def test_no_match_returns_none(self):
        f = NpcFacts(signature='r', editor_id='Robot', race='RobotRace')
        assert self.s.classify(f) is None


# ---------------------------------------------------------------------------
# Race resolution / precedence / determinism
# ---------------------------------------------------------------------------


class TestResolveRace:


    def setup_method(self):
        self.s = make_scheme(BUILTIN, SCHEME)


    def test_candidate_gate_blocks_non_furrifiable(self):
        # A SentryBot matched nothing, but even a faction match wouldn't
        # furrify it — race is outside the gate.
        f = NpcFacts(signature='b', editor_id='SentryBot01',
                     race='SentryBotRace',
                     factions=frozenset({'MinutemenFaction'}))
        assert self.s.resolve_race(f) is None


    def test_per_npc_assignment_wins(self):
        f = NpcFacts(signature='JackCabot', editor_id='JackCabot',
                     race='HumanRace', name='Settler')
        assert self.s.resolve_race(f) == 'FFOCheetahRace'


    def test_settler_resolves_to_only_target(self):
        f = NpcFacts(signature='s', editor_id='Settler17', race='HumanRace',
                     name='Settler')
        # CLASS_SETTLER has a single 100-weight target.
        assert self.s.resolve_race(f) == 'FFOFoxRace'


    def test_human_target_means_not_furrified(self):
        # Build a scheme where the only target is HumanRace.
        sc = '''
        [[class_probabilities]]
        class = "CLASS_SETTLER"
        races = [["HumanRace", 100]]
        '''
        s = make_scheme(BUILTIN, sc)
        f = NpcFacts(signature='s', editor_id='Settler01', race='HumanRace',
                     name='Settler')
        # Resolves to HumanRace -> caller treats as "leave human".
        assert s.resolve_race(f) == 'HumanRace'


    def test_deterministic(self):
        f = NpcFacts(signature='Settler42', editor_id='Settler42',
                     race='HumanRace', name='Settler')
        r1 = self.s.resolve_race(f)
        r2 = self.s.resolve_race(f)
        assert r1 == r2 and r1 == 'FFOFoxRace'


    def test_weighted_distribution_spread(self):
        # Over many distinct Minutemen signatures, the picks should land on
        # all three targets, roughly in weight order (Lykaios most common).
        from collections import Counter
        counts = Counter()
        for i in range(2000):
            f = NpcFacts(signature=f'Minuteman{i}', editor_id=f'Minuteman{i}',
                         race='HumanRace',
                         factions=frozenset({'MinutemenFaction'}))
            counts[self.s.resolve_race(f)] += 1
        assert set(counts) == {'FFOLykaiosRace', 'FFOFoxRace', 'HumanRace'}
        # Lykaios (30) should outnumber Fox (15) should outnumber Human (5).
        assert counts['FFOLykaiosRace'] > counts['FFOFoxRace'] > counts['HumanRace']


    def test_alias_members_resolve_identically(self):
        # All Kellogg records share the 'Kellogg' signature -> same race.
        ids = ['MQ203MemoryA_Kellogg', 'DN088_Kellogg', 'MQ101Kellogg']
        races = set()
        for eid in ids:
            f = NpcFacts(signature=self.s.signature_for(eid), editor_id=eid,
                         race='HumanRace', name='Settler')
            races.add(self.s.resolve_race(f))
        assert len(races) == 1


    def test_family_follows_leader(self):
        # JackCabot is pinned to Cheetah; the rest of the Cabots follow.
        facts = {
            'JackCabot': NpcFacts('JackCabot', 'JackCabot', 'HumanRace', name='Settler'),
            'EmogeneCabotOld': NpcFacts('EmogeneCabotOld', 'EmogeneCabotOld',
                                        'HumanRace', name='Settler'),
            'LorenzoCabot': NpcFacts('LorenzoCabot', 'LorenzoCabot',
                                     'HumanRace', name='Settler'),
        }
        lookup = facts.get
        for eid, f in facts.items():
            assert self.s.resolve_race(f, lookup) == 'FFOCheetahRace', eid


    def test_family_shares_race_but_not_appearance_signature(self):
        # The load-bearing distinction: a FAMILY shares only RACE; each member
        # keeps its own hashing signature so G3 gives them distinct headparts/
        # tints (relatives, not clones). An ALIAS shares the signature too
        # (same NPC -> identical appearance).
        members = ['JackCabot', 'EmogeneCabotOld', 'LorenzoCabot']
        sigs = {self.s.signature_for(m) for m in members}
        assert sigs == set(members), \
            "family members must keep distinct signatures (own EditorID)"
        # Aliases, by contrast, collapse to one shared signature.
        alias_members = ['Kellogg', 'MQ203MemoryA_Kellogg', 'DN088_Kellogg']
        alias_sigs = {self.s.signature_for(m) for m in alias_members}
        assert alias_sigs == {'Kellogg'}, \
            "alias members must share the first-entry signature"


    def test_family_shares_breed_signature(self):
        # A family shares ONE breed (Riley & Kyle are the same Deer breed): the
        # breed signature collapses to the leader, even though the appearance
        # signature stays per-member.
        members = ['JackCabot', 'EmogeneCabotOld', 'LorenzoCabot']
        breed_sigs = {self.s.breed_signature_for(m) for m in members}
        assert breed_sigs == {'JackCabot'}, \
            "family members must share the leader's breed signature"
        # ...while appearance signatures stay distinct.
        assert {self.s.signature_for(m) for m in members} == set(members)
        # Aliases share the breed signature too (it's the same NPC).
        assert self.s.breed_signature_for('DN088_Kellogg') == 'Kellogg'
        # A non-family/non-alias NPC just uses its own EditorID.
        assert self.s.breed_signature_for('SomeRandomNPC') == 'SomeRandomNPC'


    def test_family_member_can_break_away(self):
        # Give Emogene her own assignment; she leaves the family race.
        sc = SCHEME + '\n"EmogeneCabotOld" = "FFOWolfRace"\n'
        s = make_scheme(BUILTIN, sc)
        f = NpcFacts('EmogeneCabotOld', 'EmogeneCabotOld', 'HumanRace',
                     name='Settler')
        assert s.resolve_race(f) == 'FFOWolfRace'


    def test_alias_assignment_covers_all_members(self):
        # Assigning the alias's FIRST entry must apply to every member,
        # even though members have different EditorIDs.
        sc = SCHEME + '\n"Kellogg" = "FFOTigerRace"\n'
        s = make_scheme(BUILTIN, sc)
        for eid in ['MQ203MemoryA_Kellogg', 'DN088_Kellogg', 'MQ101Kellogg']:
            f = NpcFacts(signature=s.signature_for(eid), editor_id=eid,
                         race='HumanRace', name='Settler')
            assert s.resolve_race(f) == 'FFOTigerRace', eid


    def test_member_assignment_overrides_alias(self):
        # A direct assignment on a specific member beats the alias-level one.
        sc = (SCHEME + '\n"Kellogg" = "FFOTigerRace"\n'
              '"DN088_Kellogg" = "FFOWolfRace"\n')
        s = make_scheme(BUILTIN, sc)
        f = NpcFacts(signature=s.signature_for('DN088_Kellogg'),
                     editor_id='DN088_Kellogg', race='HumanRace', name='Settler')
        assert s.resolve_race(f) == 'FFOWolfRace'


    def test_unfurrified_class_returns_none(self):
        # A class with no distribution (not listed) -> None.
        s = make_scheme(BUILTIN, '''
        [[class_probabilities]]
        class = "CLASS_GHOUL"
        races = [["FFOGhoulDogRace", 100]]
        ''')
        f = NpcFacts('s', 'Settler9', 'HumanRace', name='Settler')
        assert s.resolve_race(f) is None
