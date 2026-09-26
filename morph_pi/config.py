"""Default constants for POINT target selection. Constants only, no logic."""

from __future__ import annotations

# MediaPipe hand landmark indices used to build the pointing ray.
INDEX_FINGER_MCP = 5  # ray origin
INDEX_FINGER_TIP = 8  # the ray passes through this point

# A target further than this (normalized units) from the ray is invalid.
DEFAULT_MAX_RAY_DISTANCE = 0.12
# Best and second-best distances closer than this are ambiguous.
DEFAULT_AMBIGUITY_MARGIN = 0.035
# Confidence below this is ambiguous.
DEFAULT_MIN_CONFIDENCE = 0.65
# Consecutive clear frames of the same target before it becomes stable.
DEFAULT_STABLE_FRAMES = 12
# MCP -> fingertip shorter than this gives no usable direction (no target).
DEFAULT_MIN_RAY_LENGTH = 0.01
# Confidence = w * closeness + (1 - w) * separation.
DEFAULT_CLOSENESS_WEIGHT = 0.5

# (id, label, color, x, y) for the three known tabletop targets.
DEFAULT_TARGET_SPECS: tuple[tuple[str, str, str, float, float], ...] = (
    ("blue_block", "Blue block", "blue", 0.20, 0.62),
    ("yellow_block", "Yellow block", "yellow", 0.50, 0.62),
    ("green_block", "Green block", "green", 0.80, 0.62),
)
