import dataclasses

import pytest

from morph_pi.geometry import ray_from_hand_landmarks
from morph_pi.models import Candidate, Point2D, Ray2D, SelectionResult, SelectionState, TargetConfig, TargetId
from morph_pi.selection import (
    ambiguity_reasons,
    closeness,
    combined_confidence,
    score_targets,
    select_target,
    separation,
)
from morph_pi.simulate import ambiguous_ray, clear_ray

CONFIG = TargetConfig()
S = SelectionState


def ray(ox: float, oy: float, tx: float, ty: float) -> Ray2D:
    return Ray2D(Point2D(ox, oy), Point2D(tx, ty))


def vertical_ray_at(x: float) -> Ray2D:
    """Hand at the bottom of the image pointing straight up the column x."""
    return ray(x, 0.95, x, 0.85)


# --- clear selections ---------------------------------------------------------------------


@pytest.mark.parametrize("tid", list(TargetId))
def test_clear_ray_selects_each_target(tid: TargetId) -> None:
    result = select_target(clear_ray(CONFIG.target(tid)), CONFIG)
    assert result.state is S.CANDIDATE
    assert result.target_id is tid
    assert result.stable_target is None
    assert result.confidence >= CONFIG.min_confidence
    assert result.best_candidate is not None and result.best_candidate.forward_projection > 0


@pytest.mark.parametrize(("x", "tid"), [(0.2, TargetId.BLUE_BLOCK), (0.5, TargetId.YELLOW_BLOCK), (0.8, TargetId.GREEN_BLOCK)])
def test_pointing_straight_at_a_target(x: float, tid: TargetId) -> None:
    result = select_target(vertical_ray_at(x), CONFIG)
    assert result.state is S.CANDIDATE and result.target_id is tid
    assert result.best_candidate is not None and result.best_candidate.ray_distance == pytest.approx(0.0)
    assert result.confidence == pytest.approx(1.0)  # on the ray, no rival in range


def test_best_candidate_is_the_nearest() -> None:
    # Column x=0.30: blue is 0.10 away, yellow 0.20 (out of range).
    result = select_target(vertical_ray_at(0.30), CONFIG)
    assert result.target_id is TargetId.BLUE_BLOCK
    assert result.second_candidate is None


def test_second_candidate_is_reported() -> None:
    # Widen the valid band so yellow (0.23 away) also counts; blue is 0.07 away.
    config = TargetConfig(max_ray_distance=0.3)
    result = select_target(vertical_ray_at(0.27), config)
    assert result.target_id is TargetId.BLUE_BLOCK
    assert result.second_candidate is not None
    assert result.second_candidate.target.id is TargetId.YELLOW_BLOCK
    assert result.second_candidate.ray_distance == pytest.approx(0.23)


# --- no target ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("r", "reason"),
    [
        (ray(0.5, 0.9, 0.5, 0.9), "degenerate ray"),
        (ray(0.5, 0.9, 0.505, 0.9), "degenerate ray"),  # shorter than min_ray_length 0.01
        (ray(0.5, 0.3, 0.5, 0.2), "no target ahead"),  # pointing up, targets are behind
        (ray(0.5, 0.8, 0.5, 0.9), "no target ahead"),  # pointing at the user
        (vertical_ray_at(0.05), "no target ahead"),  # blue is 0.15 away
        (vertical_ray_at(0.65), "no target ahead"),  # halfway between yellow and green: both 0.15
    ],
)
def test_no_target(r: Ray2D, reason: str) -> None:
    result = select_target(r, CONFIG)
    assert result.state is S.NONE
    assert result.best_candidate is None and result.second_candidate is None
    assert result.confidence == 0.0
    assert reason in result.reason


def test_max_ray_distance_boundary() -> None:
    inside = select_target(vertical_ray_at(0.2 + 0.119), CONFIG)
    assert inside.target_id is TargetId.BLUE_BLOCK
    assert inside.state is S.AMBIGUOUS  # valid, but far from the ray -> low confidence
    assert select_target(vertical_ray_at(0.2 + 0.121), CONFIG).state is S.NONE


def test_targets_behind_the_hand_are_never_candidates() -> None:
    # Hand 0.05 right of blue, pointing right along the target row: blue is
    # within max_ray_distance of the origin but behind it, so it must not count.
    r = ray(0.25, 0.62, 0.33, 0.62)
    ids = [c.target.id for c in score_targets(r, CONFIG)]
    assert ids == [TargetId.YELLOW_BLOCK, TargetId.GREEN_BLOCK]  # tie at 0.0 -> canonical order


def test_nearby_target_behind_the_hand_selects_nothing() -> None:
    # Hand just below blue, pointing down toward the user.
    result = select_target(ray(0.2, 0.67, 0.2, 0.75), CONFIG)
    assert result.state is S.NONE


# --- ambiguity --------------------------------------------------------------------------------------


def test_ray_between_two_targets_is_ambiguous() -> None:
    result = select_target(ambiguous_ray(CONFIG.target(TargetId.BLUE_BLOCK), CONFIG.target(TargetId.YELLOW_BLOCK)), CONFIG)
    assert result.state is S.AMBIGUOUS
    assert result.stable_target is None
    assert result.second_candidate is not None
    assert {result.target_id, result.second_candidate.target.id} == {TargetId.BLUE_BLOCK, TargetId.YELLOW_BLOCK}
    assert result.target_id is TargetId.BLUE_BLOCK  # exact tie -> canonical order
    assert "ambiguity_margin" in result.reason


def test_ambiguity_margin_boundary_is_strict() -> None:
    assert ambiguity_reasons(0.0, 0.035, 0.9, CONFIG) == []  # exactly the margin: clear
    reasons = ambiguity_reasons(0.0, 0.0349, 0.9, CONFIG)
    assert len(reasons) == 1 and "ambiguity_margin" in reasons[0]
    assert ambiguity_reasons(0.05, None, 0.9, CONFIG) == []  # no rival: margin rule does not apply


def test_min_confidence_boundary_is_strict() -> None:
    assert ambiguity_reasons(0.0, None, 0.65, CONFIG) == []
    reasons = ambiguity_reasons(0.0, None, 0.6499, CONFIG)
    assert len(reasons) == 1 and "min_confidence" in reasons[0]


def test_both_ambiguity_rules_are_reported() -> None:
    assert len(ambiguity_reasons(0.06, 0.07, 0.2, CONFIG)) == 2


def test_lone_far_candidate_is_ambiguous_by_confidence() -> None:
    result = select_target(vertical_ray_at(0.2 + 0.1), CONFIG)  # blue 0.10 away, no rival
    assert result.state is S.AMBIGUOUS
    assert result.second_candidate is None
    assert result.confidence == pytest.approx(0.5 * (1 - 0.10 / 0.12) + 0.5)
    assert "min_confidence" in result.reason and "ambiguity_margin" not in result.reason


# --- confidence ------------------------------------------------------------------------------------


def test_confidence_formula() -> None:
    assert closeness(0.0, 0.12) == 1.0
    assert closeness(0.12, 0.12) == 0.0
    assert closeness(0.5, 0.12) == 0.0  # clamped
    assert separation(0.02, None, 0.12) == 1.0
    assert separation(0.02, 0.08, 0.12) == pytest.approx(0.5)
    assert combined_confidence(0.0, None, CONFIG) == 1.0
    assert combined_confidence(0.06, 0.06, CONFIG) == pytest.approx(0.25)
    assert combined_confidence(0.03, 0.09, CONFIG) == pytest.approx(0.5 * 0.75 + 0.5 * 0.5)


def test_closeness_weight_changes_the_blend() -> None:
    only_closeness = dataclasses.replace(CONFIG, closeness_weight=1.0)
    only_separation = dataclasses.replace(CONFIG, closeness_weight=0.0)
    assert combined_confidence(0.03, 0.04, only_closeness) == pytest.approx(0.75)
    assert combined_confidence(0.03, 0.04, only_separation) == pytest.approx(0.01 / 0.12)


def test_confidence_is_bounded_and_monotonic() -> None:
    distances = [i * 0.12 / 24 for i in range(25)]
    for best in distances:
        values = [combined_confidence(best, second, CONFIG) for second in distances if second >= best]
        assert all(0.0 <= v <= 1.0 for v in values)
        assert values == sorted(values)  # a farther runner-up never lowers confidence
    lone = [combined_confidence(d, None, CONFIG) for d in distances]
    assert lone == sorted(lone, reverse=True)  # farther from the ray -> less confident


# --- responsibilities and determinism ----------------------------------------------------------------


def test_selection_never_returns_stable() -> None:
    steps = [i / 10 for i in range(1, 10)]
    rays = [ray(ox, 0.95, tx, 0.8) for ox in steps for tx in steps]
    for r in rays:
        result = select_target(r, CONFIG)
        assert result.state is not S.STABLE and result.stable_target is None


def test_selection_is_deterministic() -> None:
    r = clear_ray(CONFIG.target(TargetId.GREEN_BLOCK))
    assert select_target(r, CONFIG) == select_target(r, CONFIG)


def test_selection_from_hand_landmarks() -> None:
    landmarks = [Point2D(0.5, 0.95)] * 21
    landmarks[5], landmarks[8] = Point2D(0.8, 0.95), Point2D(0.8, 0.87)
    assert select_target(ray_from_hand_landmarks(landmarks), CONFIG).target_id is TargetId.GREEN_BLOCK


def test_select_target_rejects_non_rays() -> None:
    with pytest.raises(ValueError, match="Ray2D"):
        select_target((0.5, 0.5), CONFIG)  # type: ignore[arg-type]


# --- result invariants ---------------------------------------------------------------------------------


def candidate(tid: TargetId = TargetId.BLUE_BLOCK, distance: float = 0.01) -> Candidate:
    return Candidate(CONFIG.target(tid), distance, 0.3, closeness(distance, 0.12))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"state": S.NONE, "best_candidate": candidate()}, "NONE result must not carry candidates"),
        ({"state": S.NONE, "best_candidate": None, "confidence": 0.5}, "confidence 0.0"),
        ({"state": S.CANDIDATE, "best_candidate": None, "confidence": 0.0}, "requires a best_candidate"),
        ({"state": S.STABLE, "stable_target": None}, "stable_target"),
        ({"state": S.STABLE, "stable_target": TargetId.GREEN_BLOCK}, "stable_target"),
        ({"stable_target": TargetId.BLUE_BLOCK}, "only allowed on a STABLE"),
        ({"confidence": 1.5}, "confidence"),
        ({"second_candidate": candidate(TargetId.YELLOW_BLOCK, 0.001)}, "must not be closer"),
        ({"state": "candidate"}, "SelectionState"),
    ],
)
def test_selection_result_invariants(kwargs: dict, match: str) -> None:
    base = {
        "state": S.CANDIDATE, "best_candidate": candidate(), "second_candidate": None,
        "stable_target": None, "confidence": 0.9, "reason": "test",
    }
    with pytest.raises(ValueError, match=match):
        SelectionResult(**{**base, **kwargs})


@pytest.mark.parametrize(
    ("distance", "projection", "confidence", "match"),
    [(-0.01, 0.3, 0.5, "ray_distance"), (0.01, 0.0, 0.5, "forward_projection"), (0.01, 0.3, 1.2, "confidence")],
)
def test_candidate_validation(distance: float, projection: float, confidence: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        Candidate(CONFIG.target(TargetId.BLUE_BLOCK), distance, projection, confidence)
