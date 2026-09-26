"""MORPH desktop agent.

A local WebSocket service that receives strictly validated JSON actions
(from the MORPH Raspberry Pi vision/HCI pipeline, or the demo client) and
turns them into laptop actions. Mock mode is the default: nothing touches
the real machine unless MORPH_REAL_ACTIONS=1 is set.
"""

__version__ = "0.1.0"
