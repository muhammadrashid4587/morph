"""MORPH Pi POINT tool: inspect target configuration and run deterministic simulations.

Exit codes: 0 success, 1 a simulated scenario failed its expectations,
2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import sys

from .models import TargetConfig
from .simulate import SCENARIO_NAMES, build_scenarios, format_events, format_frame, run_scenario
from .targets import ConfigError, default_config, format_config, load_config, save_config

EXIT_OK, EXIT_SCENARIO_FAILED, EXIT_USAGE = 0, 1, 2

EXAMPLES = """\
examples:
  python -m morph_pi.app --simulate                          run every scenario
  python -m morph_pi.app --simulate --scenario blue          one scenario
  python -m morph_pi.app --simulate --scenario ambiguous -v  include per-frame reasons
  python -m morph_pi.app --show-targets                      print default targets + thresholds
  python -m morph_pi.app --save-default-targets targets.json write an editable config
  python -m morph_pi.app --config targets.json --simulate    simulate with a custom config
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m morph_pi.app",
        description="MORPH POINT target selection: pointing ray -> NONE / CANDIDATE / AMBIGUOUS / STABLE. "
        "No camera, hardware or network is used.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--simulate", action="store_true", help="run deterministic pointing scenarios")
    action.add_argument("--show-targets", action="store_true", help="print targets and thresholds")
    action.add_argument("--save-default-targets", metavar="PATH", help="write the default config as JSON")
    parser.add_argument("--config", metavar="PATH", help="target config JSON (default: built-in defaults)")
    parser.add_argument("--scenario", choices=(*SCENARIO_NAMES, "all"), help="scenario for --simulate (default: all)")
    parser.add_argument("-v", "--verbose", action="store_true", help="with --simulate, print each frame's reason")
    parser.add_argument("--force", action="store_true", help="let --save-default-targets overwrite PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.scenario or args.verbose) and not args.simulate:
        parser.error("--scenario and --verbose require --simulate")
    if args.force and not args.save_default_targets:
        parser.error("--force requires --save-default-targets")
    if args.config and args.save_default_targets:
        parser.error("--config cannot be combined with --save-default-targets")

    try:
        if args.save_default_targets:
            path = save_config(default_config(), args.save_default_targets, overwrite=args.force)
            print(f"wrote default target configuration to {path}")
            return EXIT_OK
        config = load_config(args.config) if args.config else default_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.show_targets:
        print(format_config(config, args.config or "defaults"))
        return EXIT_OK
    return simulate(config, args.scenario or "all", args.verbose)


def simulate(config: TargetConfig, scenario: str = "all", verbose: bool = False) -> int:
    try:
        scenarios = build_scenarios(config)
    except ValueError as exc:
        print(f"error: cannot build scenarios for this target layout: {exc}", file=sys.stderr)
        return EXIT_USAGE

    names = SCENARIO_NAMES if scenario == "all" else (scenario,)
    passed = 0
    for name in names:
        run = run_scenario(scenarios[name], config)
        width = max(2, len(str(len(run.frames))))
        print(f"== {name}: {run.scenario.description}")
        for frame in run.frames:
            print(format_frame(frame, width, verbose))
        if run.passed:
            passed += 1
            print(f"-- PASS  stable events: {format_events(run.events)}\n")
        else:
            print("-- FAIL  " + "\n         ".join(run.failures) + "\n")
    print(f"{passed}/{len(names)} scenario(s) passed")
    return EXIT_OK if passed == len(names) else EXIT_SCENARIO_FAILED


if __name__ == "__main__":
    sys.exit(main())
