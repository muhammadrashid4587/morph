"""MORPH's voice agent: Claude picks ONE action from a fixed allowlist.

Claude never controls servos, the arm, serial ports or anything outside
ACTIONS; its output is validated in code and anything else becomes "none".
The default executor is a dry run that only prints the chosen action.
"""
