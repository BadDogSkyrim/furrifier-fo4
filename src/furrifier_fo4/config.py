"""CLI configuration for the FO4 furrifier.

A thin front-end over `session.run` (which already does furrify + ghoul-armor
+ facegen in one call). Mirrors the Skyrim furrifier's config shape, dropping
the Skyrim-only knobs (armor/schlong toggles) and adding FO4's faction sample.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .loader import list_available_schemes


@dataclass
class FurrifierConfig:
    """All settings for one furrification run."""

    patch_filename: str = "FO4FurryPatch.esp"
    race_scheme: str = "user"
    build_facegen: bool = True
    facegen_size: Optional[int] = 1024
    limit: Optional[int] = None
    only_faction: Optional[list] = None
    only_npcs: Optional[list] = None    # restrict to these NPC EditorIDs
    # When True (default) NPCs already furrified by an earlier run are
    # re-rolled from their vanilla base. When False they're left untouched:
    # a run skips them, the preview shows their existing baked appearance.
    refurrify_existing: bool = True
    data_dir: Optional[str] = None      # READ source assets (auto-detected)
    output_dir: Optional[str] = None    # WRITE patch + FaceGenData (def: data)
    debug: bool = False
    log_file: Optional[str] = None
    # Explicit load order chosen in the GUI plugin picker. None = the game's
    # enabled (active) plugins.
    plugins: Optional[list] = None
    # Facegen bake parallelism. workers=None auto-picks min(16, cpu-1); throttle
    # forces one BELOW_NORMAL worker so the box stays usable during a long bake.
    workers: Optional[int] = None
    throttle: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "FurrifierConfig":
        patch = args.patch or cls.patch_filename
        if Path(patch).suffix.lower() not in (".esp", ".esm", ".esl"):
            patch += ".esp"
        factions = None
        if args.only_faction:
            factions = [f.strip() for f in args.only_faction.split(",")
                        if f.strip()]
        npcs = None
        if args.only_npcs:
            npcs = [n.strip() for n in args.only_npcs.split(",") if n.strip()]
        return cls(
            patch_filename=patch,
            race_scheme=args.scheme or cls.race_scheme,
            build_facegen=not args.no_facegen,
            facegen_size=args.facegen_size,
            limit=args.limit,
            only_faction=factions,
            only_npcs=npcs,
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            debug=args.debug,
            log_file=args.log_file,
            refurrify_existing=not args.no_refurrify,
            workers=args.workers,
            throttle=args.throttle,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="furrify-fo4",
        description="Batch-convert Fallout 4 NPCs to furry races using esplib.",
    )
    parser.add_argument("--patch", default="FO4FurryPatch.esp",
                        help="Output patch filename (default: FO4FurryPatch.esp)")
    scheme_kwargs = {}
    discovered = list_available_schemes()
    if discovered:
        scheme_kwargs["choices"] = discovered
    parser.add_argument("--scheme", default="user", type=str.lower,
                        help="Race-assignment scheme (a *.toml in schemes/). "
                             "Default: user.", **scheme_kwargs)
    parser.add_argument("--resources", dest="data_dir", metavar="DIR",
                        help="Override resource dir searched FIRST for plugins "
                             "and assets, before the game's Data folder (e.g. a "
                             "mod or test fixtures). Falls back to the game Data "
                             "for anything not found there. Defaults to the game "
                             "Data folder (auto-detected) when omitted.")
    parser.add_argument("-o", "--output", dest="output_dir", metavar="DIR",
                        help="Directory to WRITE the patch + FaceGenData "
                             "(defaults to the game Data dir; point at a mod "
                             "manager's staging folder to keep Data clean)")
    parser.add_argument("--no-facegen", action="store_true",
                        help="Skip baking per-NPC FaceCustomization textures "
                             "and facegeom nifs")
    parser.add_argument("--facegen-size", type=int,
                        choices=(256, 512, 1024, 2048, 4096), default=1024,
                        help="Square edge (px) for baked FaceCustomization "
                             "diffuse (default: 1024)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Furrify at most N NPCs (quick test runs)")
    parser.add_argument("--no-refurrify", action="store_true",
                        help="Leave NPCs an earlier furrifier run already "
                             "furrified untouched (skip them) instead of "
                             "re-rolling them from their vanilla base")
    parser.add_argument("--npcs", dest="only_npcs", metavar="EDID[,EDID...]",
                        help="Restrict to NPCs with these EditorIDs "
                             "(comma-separated) — e.g. John,RosalindOrman. "
                             "For focused testing of specific characters.")
    parser.add_argument("--faction", dest="only_faction", metavar="EDID[,EDID...]",
                        help="Restrict to members of these faction EditorIDs "
                             "(comma-separated) — a focused in-game sample, "
                             "e.g. SettlementDiamondCity,SettlementGoodneighbor")
    parser.add_argument("--workers", type=int, metavar="N",
                        help="FaceGen bake worker processes "
                             "(default: auto, min(16, cpu-1))")
    parser.add_argument("--throttle", action="store_true",
                        help="Bake FaceGen with a single BELOW_NORMAL-priority "
                             "worker so the machine stays usable")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--log", dest="log_file", metavar="FILE",
                        help="Write log to FILE")
    return parser


def normalize_argv(argv: list) -> list:
    """Lowercase switch names (not their values) so --Scheme etc. work."""
    out = []
    for tok in argv:
        if tok.startswith("-") and len(tok) > 1:
            if "=" in tok:
                flag, _, val = tok.partition("=")
                out.append(f"{flag.lower()}={val}")
            else:
                out.append(tok.lower())
        else:
            out.append(tok)
    return out


def setup_logging(config: FurrifierConfig) -> None:
    level = logging.DEBUG if config.debug else logging.INFO
    handlers = [logging.StreamHandler()]
    if config.log_file:
        handlers.append(logging.FileHandler(config.log_file))
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s",
                        handlers=handlers, force=True)
    if not config.debug:
        # PyNifly's import-time basicConfig is chatty at DEBUG.
        logging.getLogger("pynifly").setLevel(logging.WARNING)
