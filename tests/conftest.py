import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = os.path.join(ROOT, "legacy")

# The archived simulator (legacy/main.py) opens its data file by relative path and
# calls logging.basicConfig at import time, which would overwrite its log file. Run
# from legacy/ and give the root logger a handler so basicConfig is a no-op.
# bar_opt uses absolute paths and is unaffected.
os.chdir(LEGACY)
sys.path.insert(0, ROOT)
sys.path.insert(1, LEGACY)
logging.getLogger().addHandler(logging.NullHandler())


def no_walk(**values):
    """Config without walking, for tests of the economy itself."""
    from bar_opt.config import EconConfig

    return EconConfig(walk_base=0.0, walk_mex_near=0.0, walk_mex_far=0.0, **values)
