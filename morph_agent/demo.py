"""Talk to MORPH: press Enter, speak, press Enter; MORPH answers out loud and prints its action.

    python -m morph_agent.demo                      dry run (default): prints the action, sends nothing
    python -m morph_agent.demo --mode presentation --target blue
    python -m morph_agent.demo --live               also send the action to the laptop agent
    python -m morph_agent.demo --type               type instead of speaking (no microphone)

Needs ELEVENLABS_API_KEY in .env; GEMINI_API_KEY is optional (keyword commands without it). Ctrl+C to quit.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from morph_voice.audio import AudioUnavailable
from morph_voice.elevenlabs import ElevenLabsError
from morph_voice.env import MissingKeyError

from .actions import MODES, TARGETS, MorphState, apply
from .agent import Agent
from .executor import DryRunExecutor, LaptopExecutor


def run_turn(
    listen: Callable[[], str],
    agent: Any,
    state: MorphState,
    executor: Any,
    say: Callable[[str], None],
    out: Callable[[str], None] = print,
) -> MorphState:
    """One exchange: listen -> the agent picks an action -> execute (or dry run) -> speak the reply."""
    transcript = listen()
    out(f"You said: {transcript!r}")
    action = agent.decide(transcript, state)
    source = getattr(agent, "last_source", "gemini")
    if source == "keywords":
        out(f"(keyword fallback{': ' + agent.last_error if agent.last_error else ''})")
    elif source == "rate_limited":
        out(f"({agent.last_error})")
    out(f"MORPH action: {action.action}" + (f" (mode={action.mode})" if action.mode else "") + f" | reply: {action.reply!r}")
    executor.execute(action)
    say(action.reply)
    return apply(action, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m morph_agent.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default="robot_targeting", help="MORPH's starting mode")
    parser.add_argument("--target", choices=TARGETS, help="pretend this block is locked")
    parser.add_argument("--live", action="store_true", help="send actions to the laptop agent (default: dry run)")
    parser.add_argument("--type", action="store_true", help="type commands instead of speaking")
    args = parser.parse_args(argv)

    from morph_voice import speech

    state = MorphState(args.mode, args.target)
    executor = LaptopExecutor() if args.live else DryRunExecutor()
    agent = Agent.from_env()
    try:
        voice = speech.default_voice()
    except (MissingKeyError, ElevenLabsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    listen = (lambda: input("You (type): ")) if args.type else voice.listen
    print(f"MORPH voice demo ({'LIVE' if args.live else 'dry run'}). State: {state}. Ctrl+C to quit.")
    while True:
        try:
            state = run_turn(listen, agent, state, executor, voice.say)
        except KeyboardInterrupt:
            print("\nBye.")
            return 0
        except (ElevenLabsError, AudioUnavailable, ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            if isinstance(exc, AudioUnavailable):
                return 1


if __name__ == "__main__":
    sys.exit(main())
