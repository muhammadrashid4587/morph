"""MORPH's voice agent: Gemini picks ONE action from a fixed allowlist.

The model never controls servos, the arm, serial ports or anything outside
ACTIONS; its output is validated in code and anything else becomes "none".
Without a key (or when the API fails) a keyword fallback decides instead.
The default executor is a dry run that only prints the chosen action.
"""
