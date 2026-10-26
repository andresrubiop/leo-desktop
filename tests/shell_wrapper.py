"""Add a second real virtual output to the isolated Shell test, never the user session."""
import os
import sys

os.execv(sys.argv[1], sys.argv[1:] + ["--virtual-monitor", "1440x900"])
