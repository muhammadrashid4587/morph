import pytest

from morph_pi.models import Point2D, Ray2D, SelectionResult, SelectionState, TargetConfig, TargetId
from morph_pi.selection import select_target
from morph_pi.simulate import ambiguous_ray, clear_ray
from morph_pi.smoothing import TemporalSmoother

CONFIG = TargetConfig()
S = SelectionState
N = CONFIG.stable_frames

BLUE = select_target(clear_ray(CONFIG.target(TargetId.BLUE_BLOCK)), CONFIG)
YELLOW = select_target(clear_ray(CONFIG.target(TargetId.YELLOW_BLOCK)), CONFIG)
NONE = select_target(Ray2D(Point2D(0.5, 0.9), Point2D(0.5, 0.9)), CONFIG)
AMBIGUOUS = select_target(
    ambiguous_ray(CONFIG.target(TargetId.BLUE_BLOCK), CONFIG.target(TargetId.YELLOW_BLOCK)), CONFIG
)


def feed(smoother: TemporalSmoother, frame: SelectionResult, count: int) -> list[SelectionResult]:
    return [smoother.update(frame) for _ in range(count)]


def stable_events(results: list[SelectionResult]) -> list[TargetId]:
    return [r.stable_target for r in results if r.state is S.STABLE and r.stable_target is not None]


def test_fixture_frames_have_expected_states() -> None:
    assert (BLUE.state, YELLOW.state, NONE.state, AMBIGUOUS.state) == (S.CANDIDATE, S.CANDIDATE, S.NONE, S.AMBIGUOUS)


def test_becomes_stable_on_the_twelfth_frame() -> None:
    smoother = TemporalSmoother(N)
    first = feed(smoother, BLUE, N - 1)
    assert all(r.state is S.CANDIDATE and r.stable_target is None for r in first)
    assert smoother.streak == N - 1
    twelfth = smoother.update(BLUE)
    assert twelfth.state is S.STABLE
    assert twelfth.stable_target is TargetId.BLUE_BLOCK
    assert twelfth.confidence == BLUE.confidence
    assert smoother.streak == N and smoother.latched


def test_stable_emits_exactly_once_while_pointing() -> None:
    results = feed(TemporalSmoother(N), BLUE, 100)
    assert stable_events(results) == [TargetId.BLUE_BLOCK]
    assert results[N - 1].state is S.STABLE
    assert all(r.state is S.CANDIDATE and "already emitted" in r.reason for r in results[N:])


@pytest.mark.parametrize("breaker", [NONE, AMBIGUOUS], ids=["none", "ambiguous"])
def test_none_or_ambiguous_resets_the_streak(breaker: SelectionResult) -> None:
    smoother = TemporalSmoother(N)
    feed(smoother, BLUE, N - 1)
    assert smoother.update(breaker) is breaker  # passes through untouched
    assert smoother.streak == 0 and smoother.current_target is None
    results = feed(smoother, BLUE, N - 1)
    assert stable_events(results) == []  # 11 + 11 never adds up across a break
    assert smoother.update(BLUE).state is S.STABLE


def test_candidate_change_resets_the_streak() -> None:
    smoother = TemporalSmoother(N)
    feed(smoother, BLUE, N - 1)
    smoother.update(YELLOW)
    assert smoother.current_target is TargetId.YELLOW_BLOCK and smoother.streak == 1
    results = feed(smoother, YELLOW, N - 1)
    assert stable_events(results) == [TargetId.YELLOW_BLOCK]
    assert results[-1].state is S.STABLE


def test_explicit_reset() -> None:
    smoother = TemporalSmoother(N)
    feed(smoother, BLUE, N - 1)
    smoother.reset()
    assert (smoother.streak, smoother.current_target, smoother.latched) == (0, None, False)
    assert stable_events(feed(smoother, BLUE, N - 1)) == []


@pytest.mark.parametrize("release", ["none", "ambiguous", "reset", "other_target"])
def test_same_target_can_become_stable_again_after_release(release: str) -> None:
    smoother = TemporalSmoother(N)
    events = stable_events(feed(smoother, BLUE, N + 5))
    if release == "none":
        smoother.update(NONE)
    elif release == "ambiguous":
        smoother.update(AMBIGUOUS)
    elif release == "reset":
        smoother.reset()
    else:
        smoother.update(YELLOW)
    assert not smoother.latched
    events += stable_events(feed(smoother, BLUE, N))
    assert events == [TargetId.BLUE_BLOCK, TargetId.BLUE_BLOCK]


def test_switching_targets_while_latched_emits_the_new_target() -> None:
    smoother = TemporalSmoother(N)
    events = stable_events(feed(smoother, BLUE, N + 3) + feed(smoother, YELLOW, N))
    assert events == [TargetId.BLUE_BLOCK, TargetId.YELLOW_BLOCK]


def test_debug_state_is_exposed() -> None:
    smoother = TemporalSmoother(N)
    assert (smoother.streak, smoother.current_target, smoother.latched) == (0, None, False)
    feed(smoother, BLUE, 3)
    assert (smoother.streak, smoother.current_target, smoother.latched) == (3, TargetId.BLUE_BLOCK, False)
    result = smoother.update(BLUE)
    assert "streak 4/12" in result.reason


def test_stable_frames_of_one_emits_immediately() -> None:
    smoother = TemporalSmoother(1)
    assert smoother.update(BLUE).state is S.STABLE
    assert smoother.update(BLUE).state is S.CANDIDATE


@pytest.mark.parametrize("bad", [0, -3, True, 1.5, "12"])
def test_invalid_stable_frames(bad: object) -> None:
    with pytest.raises(ValueError, match="stable_frames"):
        TemporalSmoother(bad)  # type: ignore[arg-type]


def test_rejects_stable_or_foreign_input() -> None:
    smoother = TemporalSmoother(1)
    stable = smoother.update(BLUE)
    with pytest.raises(ValueError, match="not STABLE"):
        TemporalSmoother(N).update(stable)
    with pytest.raises(ValueError, match="SelectionResult"):
        TemporalSmoother(N).update("candidate")  # type: ignore[arg-type]
