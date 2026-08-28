"""Fallout 4 furrifier — distributes furry races across NPCs by class.

Unlike Skyrim (≈1:1 vanilla→furry race mapping), FO4 has essentially one
playable race (HumanRace), so furry races are distributed across NPC *classes*
by weight. See PLAN_FO4_SCHEME.md for the configuration format.
"""

# major.minor.patch, edited by hand. The build number is a separate
# counter (build_number.json -> build_info.BUILD); it is deliberately not
# part of this string. See build_info.py.
__version__ = "1.2.0"
