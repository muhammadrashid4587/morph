import dataclasses
import json
import re
from pathlib import Path

import pytest

from morph_pi import app
from morph_pi.models import Point2D, SelectionState, TargetConfig, TargetId
from morph_pi.simulate import (
    SCENARIO_NAMES,
    FrameRecord,
    build_scenarios,
    format_frame,
    run_scenario,
)
from morph_pi.targets import config_to_dict

CONFIG = TargetConfig()
S = SelectionState
SCENARIOS = build_scenarios(CONFIG)
FRAME_LINE = re.compile(
    r"^frame=\d{2,} state=(NONE|CANDIDATE|STABLE|AMBIGUOUS) target=([A-Z_]+|-) confidence=\d\.\d{2} streak=\d+( \(held\))?$"
)


def run(name: str, config: TargetConfig = CONFIG):
    return run_scenario(build_scenarios(config)[name], config)


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_every_scenario_passes(name: str) -> None:
    result = run(name)
    assert result.passed, result.failures


@pytest.mark.parametrize(
    ("name", "tid"),
    [("blue", TargetId.BLUE_BLOCK), ("yellow", TargetId.YELLOW_BLOCK), ("green", TargetId.GREEN_BLOCK)],
)
def test_clear_scenarios_become_stable_exactly_once(name: str, tid: TargetId) -> None:
    result = run(name)
    assert result.events == ((12, tid),)
    assert len(result.frames) > 12  # keeps pointing after STABLE
    assert [f.result.state for f in result.frames].count(S.STABLE) == 1
    assert all(f.result.target_id is tid for f in result.frames)


def test_ambiguous_scenario_is_never_stable() -> None:
    result = run("ambiguous")
    assert result.events == ()
    assert {f.result.state for f in result.frames} == {S.AMBIGUOUS}
    assert all(f.streak == 0 for f in result.frames)


def test_none_scenario_is_never_stable() -> None:
    result = run("none")
    assert result.events == ()
    assert {f.result.state for f in result.frames} == {S.NONE}
    reasons = {f.result.reason.split(":")[0] for f in result.frames}
    assert reasons == {"degenerate ray", "no target ahead of the ray within max_ray_distance 0.12"}


def test_reacquire_requires_a_fresh_streak() -> None:
    result = run("reacquire")
    states = [f.result.state for f in result.frames]
    assert states[:8] == [S.CANDIDATE] * 8
    assert states[8:11] == [S.NONE] * 3
    assert result.frames[11].streak == 1  # counting starts over
    assert result.events == ((23, TargetId.BLUE_BLOCK),)  # 8 + 3 + a fresh 12


def test_release_allows_a_second_stable_event() -> None:
    assert run("release").events == ((12, TargetId.BLUE_BLOCK), (30, TargetId.BLUE_BLOCK))


def test_runs_are_deterministic() -> None:
    assert run("release") == run("release")
    assert build_scenarios(CONFIG) == build_scenarios(CONFIG)


def test_scenarios_follow_a_custom_config() -> None:
    moved = tuple(
        dataclasses.replace(t, center=Point2D(t.center.x * 0.8 + 0.1, 0.45)) for t in CONFIG.targets
    )
    config = TargetConfig(targets=moved, stable_frames=5)
    for name in SCENARIO_NAMES:
        result = run(name, config)
        assert result.passed, (name, result.failures)
    assert run("blue", config).events == ((5, TargetId.BLUE_BLOCK),)


def test_failed_expectations_are_reported() -> None:
    wrong = dataclasses.replace(SCENARIOS["blue"], expected_events=((3, TargetId.GREEN_BLOCK),))
    result = run_scenario(wrong, CONFIG)
    assert not result.passed
    assert "expected STABLE events [GREEN_BLOCK@3], got [BLUE_BLOCK@12]" in result.failures[0]
    strict = dataclasses.replace(SCENARIOS["blue"], allowed_states=frozenset({S.CANDIDATE}))
    assert any("unexpected state STABLE at frame 12" in f for f in run_scenario(strict, CONFIG).failures)


# --- output format --------------------------------------------------------------------------------


def test_frame_format_matches_spec() -> None:
    frames = run("blue").frames
    assert format_frame(frames[0]) == "frame=01 state=CANDIDATE target=BLUE_BLOCK confidence=0.96 streak=1"
    assert format_frame(frames[11]) == "frame=12 state=STABLE target=BLUE_BLOCK confidence=0.96 streak=12"
    assert format_frame(frames[12]).endswith("streak=13 (held)")
    for name in SCENARIO_NAMES:
        for frame in run(name).frames:
            assert FRAME_LINE.match(format_frame(frame)), format_frame(frame)


def test_verbose_format_adds_the_reason() -> None:
    frame = run("none").frames[0]
    assert format_frame(frame, verbose=True).endswith("| " + frame.result.reason)
    assert "target=-" in format_frame(frame)
    assert isinstance(frame, FrameRecord)


# --- CLI ------------------------------------------------------------------------------------------------


def test_cli_simulate_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["--simulate"]) == 0
    out = capsys.readouterr().out
    assert f"{len(SCENARIO_NAMES)}/{len(SCENARIO_NAMES)} scenario(s) passed" in out
    assert "FAIL" not in out


def test_cli_simulate_blue(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["--simulate", "--scenario", "blue"]) == 0
    lines = capsys.readouterr().out.splitlines()
    frame_lines = [line for line in lines if line.startswith("frame=")]
    assert frame_lines[0] == "frame=01 state=CANDIDATE target=BLUE_BLOCK confidence=0.96 streak=1"
    assert "frame=12 state=STABLE target=BLUE_BLOCK confidence=0.96 streak=12" in frame_lines
    assert sum("state=STABLE" in line for line in frame_lines) == 1


def test_cli_simulate_ambiguous(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["--simulate", "--scenario", "ambiguous", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "state=STABLE" not in out and "state=AMBIGUOUS" in out and "ambiguity_margin" in out


def test_cli_simulate_with_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = config_to_dict(CONFIG)
    data["thresholds"]["stable_frames"] = 6
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(data))
    assert app.main(["--config", str(path), "--simulate", "--scenario", "green"]) == 0
    assert "frame=06 state=STABLE target=GREEN_BLOCK" in capsys.readouterr().out


def test_cli_simulate_invalid_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json")
    assert app.main(["--config", str(path), "--simulate"]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_cli_unknown_scenario_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        app.main(["--simulate", "--scenario", "purple"])
    assert info.value.code == 2


def test_cli_reports_scenario_failure_and_build_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = dict(SCENARIOS)
    broken["blue"] = dataclasses.replace(SCENARIOS["blue"], expected_events=())
    monkeypatch.setattr(app, "build_scenarios", lambda config: broken)
    assert app.main(["--simulate", "--scenario", "blue"]) == 1
    assert "-- FAIL" in capsys.readouterr().out

    def explode(config: TargetConfig) -> dict:
        raise ValueError("Point2D.x must be within [0.0, 1.0], got 1.2")

    monkeypatch.setattr(app, "build_scenarios", explode)
    assert app.main(["--simulate"]) == 2
    assert "cannot build scenarios" in capsys.readouterr().err
