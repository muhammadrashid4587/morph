"""MORPH's voice: ElevenLabs text-to-speech (say) and push-to-talk speech-to-text (listen).

    from morph_voice import say, listen
    say("Locked on blue")
    text = listen()          # press Enter, speak, press Enter

Keys come only from the repo's .env file (git-ignored) and are never printed.
Audio packages (sounddevice) are imported lazily, only when audio is played or recorded.
"""

from .speech import PHRASES, listen, say

__all__ = ["PHRASES", "listen", "say"]
