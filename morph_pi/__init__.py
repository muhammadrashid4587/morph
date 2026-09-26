"""MORPH Pi-side POINT logic.

Pure, deterministic Python: turns an index-finger pointing ray (MediaPipe
hand landmarks 5 -> 8, normalized image coordinates) into a semantic
target selection (NONE / CANDIDATE / AMBIGUOUS / STABLE). It never touches
cameras, serial ports, robot hardware or the network.
"""

__version__ = "0.1.0"
