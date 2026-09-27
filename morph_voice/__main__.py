"""MORPH voice tools.

    python -m morph_voice --cache-phrases     pre-generate the fixed phrases (needs ELEVENLABS_API_KEY in .env)
    python -m morph_voice --say "Hello"       speak a sentence
    python -m morph_voice --listen            push-to-talk once and print the transcript
    python -m morph_voice --devices           list audio devices (for MORPH_AUDIO_INPUT / MORPH_AUDIO_OUTPUT)
"""

from __future__ import annotations

import argparse
import sys

from .audio import AudioUnavailable, list_devices
from .elevenlabs import ElevenLabsError
from .env import MissingKeyError
from .speech import PHRASES, default_voice


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m morph_voice", description="MORPH voice (ElevenLabs).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cache-phrases", action="store_true", help="pre-generate the fixed demo phrases")
    group.add_argument("--say", metavar="TEXT", help="speak TEXT")
    group.add_argument("--listen", action="store_true", help="push-to-talk once, print the transcript")
    group.add_argument("--devices", action="store_true", help="list audio devices")
    args = parser.parse_args(argv)
    try:
        if args.devices:
            print(list_devices())
        elif args.cache_phrases:
            voice = default_voice()
            for phrase in PHRASES:
                voice.speech_audio(phrase)
                print(f"cached: {phrase}")
        elif args.say:
            default_voice().say(args.say)
        else:
            print(f"You said: {default_voice().listen()!r}")
    except (MissingKeyError, ElevenLabsError, AudioUnavailable, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
