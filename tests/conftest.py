import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# main.py opens its data file by relative path and calls logging.basicConfig at
# import time, which would overwrite simulationV2_log.txt. A root handler makes
# basicConfig a no-op.
os.chdir(ROOT)
sys.path.insert(0, ROOT)
logging.getLogger().addHandler(logging.NullHandler())
