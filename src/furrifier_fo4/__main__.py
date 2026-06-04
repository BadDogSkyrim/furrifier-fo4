"""`python -m furrifier_fo4` -> the CLI."""

import sys

from .main import main

# The guard is REQUIRED: the facegen bake spawns a ProcessPoolExecutor, and
# multiprocessing's spawn re-imports this module in each worker. Without it,
# every worker would re-run the CLI (a fork bomb).
if __name__ == "__main__":
    sys.exit(main())
