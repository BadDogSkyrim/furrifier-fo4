"""FO4 furrifier CLI entry point.

Parses args, runs `session.run` (furrify + ghoul-armor + facegen), prints a
stats summary. Thin by design — the pipeline lives in session.py.
"""

from __future__ import annotations

import logging
import sys
import time
import threading
from pathlib import Path
from typing import Optional

from .config import FurrifierConfig, build_parser, normalize_argv, setup_logging
from . import session
from .session import ProgressCallback, CancelledError

log = logging.getLogger(__name__)


def run_furrification(
        config: FurrifierConfig,
        world=None,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None) -> int:
    log.info("Fallout 4 Furrifier")
    log.info("  Scheme:        %s", config.race_scheme)
    log.info("  Patch:         %s", config.patch_filename)
    log.info("  Build FaceGen: %s", config.build_facegen)
    if config.only_faction:
        log.info("  Only factions: %s", ", ".join(config.only_faction))
    if config.limit is not None:
        log.info("  Limit:         %d", config.limit)

    t0 = time.perf_counter()
    try:
        stats = session.run(
            config.race_scheme,
            patch_name=config.patch_filename,
            plugins=config.plugins,
            data_dir=config.data_dir,
            output_dir=config.output_dir,
            limit=config.limit,
            only_faction=config.only_faction,
            only_npcs=config.only_npcs,
            bake_facegen=config.build_facegen,
            facegen_size=config.facegen_size,
            refurrify_existing=config.refurrify_existing,
            variant_expansion=config.variant_expansion,
            emit_esl=config.emit_esl,
            pack=config.pack,
            workers=config.workers,
            throttle=config.throttle,
            world=world,
            progress=progress,
            cancel_event=cancel_event,
        )
    except CancelledError:
        # The GUI worker sets the cancel flag; let it propagate so the worker
        # reports a clean cancel instead of a failure. The CLI never cancels.
        raise
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        log.exception("Furrification failed")
        return 1

    mins, secs = divmod(int(round(time.perf_counter() - t0)), 60)
    fg = stats.get("facegen") or {}
    log.info("  Furrified:  %d / %d NPCs", stats["furrified"], stats["total"])
    log.info("  Left human: %d   gated: %d   no-child-race: %d   preserved: %d",
             stats["left_human"], stats["gated"], stats["no_child_race"],
             stats.get("preserved", 0))
    if stats.get("minimal_children"):
        log.info("  Children (minimal: race+skin only, no headparts/tints): %d",
                 stats["minimal_children"])
    log.info("  Templated leaves: %d   -> trait-owners furrified: %d",
             stats.get("templated", 0), stats.get("owner_furrified", 0))
    log.info("  Owners expanded:  %d   -> variants minted: %d",
             stats.get("expanded_owners", 0), stats.get("variants", 0))
    log.info("  ARMAs fixed: %d", stats["armas_patched"])
    if config.emit_esl:
        kind = "ESL (light)" if stats.get("esl") else "ESP (too big for ESL)"
        log.info("  Plugin type: %s   new records: %d",
                 kind, stats.get("new_records", 0))
    if fg:
        log.info("  FaceGen: %d textures, %d nifs (%d failed)",
                 fg.get("baked", 0), fg.get("nif", 0), fg.get("nif_failed", 0))
    if stats.get("packed"):
        log.info("  Packed into: %s",
                 ", ".join(Path(p).name for p in stats["packed"]))
    by_race = stats.get("race_counts") or {}
    for race, n in sorted(by_race.items(), key=lambda kv: -kv[1]):
        log.info("      %-22s %d", race, n)
    # The timing line is intentionally LAST so it's the final thing in the log
    # pane / console after every pass and the stats summary.
    log.info("Done in %dm %02ds", mins, secs)
    return 0


def main() -> int:
    # Required before the facegen ProcessPoolExecutor in a frozen build; no-op
    # from source.
    import multiprocessing
    multiprocessing.freeze_support()
    parser = build_parser()
    args = parser.parse_args(normalize_argv(sys.argv[1:]))
    config = FurrifierConfig.from_args(args)
    setup_logging(config)
    return run_furrification(config)


if __name__ == "__main__":
    sys.exit(main())
