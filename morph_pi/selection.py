"""Per-frame geometric target selection.

select_target() looks at ONE ray and answers NONE, CANDIDATE or AMBIGUOUS.
It never answers STABLE: temporal stability belongs to smoothing.py.

Confidence (deterministic, in [0, 1]):
    closeness  = 1 - best_distance / max_ray_distance
    separation = (second_distance - best_distance) / max_ray_distance
                 (1.0 when there is no second valid target)
    confidence = w * closeness + (1 - w) * separation,  w = closeness_weight

Ambiguous when best and second-best distances differ by less than
ambiguity_margin, OR confidence is below min_confidence.
"""

from __future__ import annotations

from .geometry import forward_projection, is_degenerate, ray_distance, ray_length
from .models import (
    TARGET_ORDER,
    Candidate,
    Ray2D,
    SelectionResult,
    SelectionState,
    TargetConfig,
)

_DEFAULT_CONFIG = TargetConfig()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def closeness(distance: float, max_distance: float) -> float:
    """1.0 on the ray, falling linearly to 0.0 at max_distance."""
    return _clamp01(1.0 - distance / max_distance)


def separation(best_distance: float, second_distance: float | None, max_distance: float) -> float:
    """How clearly the best target beats the runner-up (1.0 if there is none)."""
    if second_distance is None:
        return 1.0
    return _clamp01((second_distance - best_distance) / max_distance)


def combined_confidence(best_distance: float, second_distance: float | None, config: TargetConfig) -> float:
    w = config.closeness_weight
    return _clamp01(
        w * closeness(best_distance, config.max_ray_distance)
        + (1.0 - w) * separation(best_distance, second_distance, config.max_ray_distance)
    )


def ambiguity_reasons(
    best_distance: float, second_distance: float | None, confidence: float, config: TargetConfig
) -> list[str]:
    """Why a selection is ambiguous; empty when it is clear."""
    reasons: list[str] = []
    if second_distance is not None:
        gap = second_distance - best_distance
        if gap < config.ambiguity_margin:
            reasons.append(f"distance gap {gap:.3f} < ambiguity_margin {config.ambiguity_margin}")
    if confidence < config.min_confidence:
        reasons.append(f"confidence {confidence:.2f} < min_confidence {config.min_confidence}")
    return reasons


def score_targets(ray: Ray2D, config: TargetConfig = _DEFAULT_CONFIG) -> list[Candidate]:
    """Every valid target (ahead of the ray, within max_ray_distance), nearest first.

    Ties keep canonical target order (blue, yellow, green), so results are deterministic.
    The ray must not be degenerate (check is_degenerate first).
    """
    candidates: list[Candidate] = []
    for target in config.targets:
        projection = forward_projection(target.center, ray)
        if projection <= 0.0:
            continue  # behind the hand
        distance = ray_distance(target.center, ray)
        if distance > config.max_ray_distance:
            continue
        candidates.append(
            Candidate(target, distance, projection, closeness(distance, config.max_ray_distance))
        )
    candidates.sort(key=lambda c: (c.ray_distance, TARGET_ORDER.index(c.target.id)))
    return candidates


def select_target(ray: Ray2D, config: TargetConfig = _DEFAULT_CONFIG) -> SelectionResult:
    """Classify one frame's pointing ray. Never returns STABLE."""
    if not isinstance(ray, Ray2D):
        raise ValueError(f"ray must be a Ray2D, got {type(ray).__name__}")
    if is_degenerate(ray, config.min_ray_length):
        return _none(f"degenerate ray: length {ray_length(ray):.4f} <= min_ray_length {config.min_ray_length}")

    candidates = score_targets(ray, config)
    if not candidates:
        return _none(f"no target ahead of the ray within max_ray_distance {config.max_ray_distance}")

    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    second_distance = None if second is None else second.ray_distance
    confidence = combined_confidence(best.ray_distance, second_distance, config)
    reasons = ambiguity_reasons(best.ray_distance, second_distance, confidence, config)
    if reasons:
        rivals = best.target.id.name + ("" if second is None else f" vs {second.target.id.name}")
        return SelectionResult(
            SelectionState.AMBIGUOUS, best, second, None, confidence, f"ambiguous ({rivals}): {'; '.join(reasons)}"
        )
    return SelectionResult(
        SelectionState.CANDIDATE, best, second, None, confidence,
        f"clear: {best.target.id.name} is {best.ray_distance:.3f} from the ray",
    )


def _none(reason: str) -> SelectionResult:
    return SelectionResult(SelectionState.NONE, None, None, None, 0.0, reason)
