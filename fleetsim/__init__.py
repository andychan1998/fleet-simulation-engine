"""Fleet simulation engine: a discrete-time simulation of ships, driven by JSON commands."""

import sys

# Checked here, before any other module is imported, so an old Python gets a
# clear message instead of a syntax error from newer code.
if sys.version_info < (3, 9):
    sys.exit("fleetsim requires Python 3.9 or newer")

from fleetsim.protocol import process_line  # noqa: E402
from fleetsim.simulation import Simulation  # noqa: E402

__all__ = ["Simulation", "process_line"]
