"""Target configuration: defaults, JSON load/save, and display.

JSON format (targets keyed by id; label/color and every threshold optional):

    {
      "targets": {
        "blue_block":   {"x": 0.2, "y": 0.62, "label": "Blue block", "color": "blue"},
        "yellow_block": {"x": 0.5, "y": 0.62},
        "green_block":  {"x": 0.8, "y": 0.62}
      },
      "thresholds": {"max_ray_distance": 0.12, "stable_frames": 12}
    }

Unknown keys, duplicate keys, unknown or missing target ids, and invalid
numbers are all rejected with a ConfigError naming the offending field.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from .config import DEFAULT_TARGET_SPECS
from .models import Point2D, Target, TargetConfig, TargetId

THRESHOLD_FIELDS: tuple[str, ...] = (
    "max_ray_distance",
    "ambiguity_margin",
    "min_confidence",
    "stable_frames",
    "min_ray_length",
    "closeness_weight",
)
_TOP_LEVEL_KEYS = frozenset({"targets", "thresholds"})
_TARGET_KEYS = frozenset({"x", "y", "label", "color"})
_DEFAULT_LABELS = {tid: (label, color) for tid, label, color, _, _ in DEFAULT_TARGET_SPECS}


class ConfigError(ValueError):
    """A target configuration file or object is invalid."""


def default_config() -> TargetConfig:
    return TargetConfig()


# --- dict <-> config ------------------------------------------------------------------


def config_to_dict(config: TargetConfig) -> dict[str, Any]:
    return {
        "targets": {
            t.id.value: {"x": t.center.x, "y": t.center.y, "label": t.label, "color": t.color}
            for t in config.targets
        },
        "thresholds": {name: getattr(config, name) for name in THRESHOLD_FIELDS},
    }


def config_from_dict(data: object) -> TargetConfig:
    obj = _object(data, "config")
    _reject_unknown(obj, _TOP_LEVEL_KEYS, "config")
    if "targets" not in obj:
        raise ConfigError("config: missing required key 'targets'")
    targets = [_target_from_dict(key, value) for key, value in _object(obj["targets"], "targets").items()]
    thresholds = _object(obj.get("thresholds", {}), "thresholds")
    _reject_unknown(thresholds, frozenset(THRESHOLD_FIELDS), "thresholds")
    try:
        config = TargetConfig(targets=tuple(targets))
    except ValueError as exc:
        raise ConfigError(f"targets: {exc}") from None
    try:
        return dataclasses.replace(config, **thresholds)
    except ValueError as exc:
        raise ConfigError(f"thresholds: {exc}") from None


def _target_from_dict(key: str, value: object) -> Target:
    where = f"targets.{key}"
    try:
        target_id = TargetId(key)
    except ValueError:
        expected = ", ".join(t.value for t in TargetId)
        raise ConfigError(f"{where}: unknown target id (expected one of: {expected})") from None
    entry = _object(value, where)
    _reject_unknown(entry, _TARGET_KEYS, where)
    for axis in ("x", "y"):
        if axis not in entry:
            raise ConfigError(f"{where}: missing required key '{axis}'")
    default_label, default_color = _DEFAULT_LABELS[target_id.value]
    try:
        return Target(
            id=target_id,
            label=entry.get("label", default_label),
            color=entry.get("color", default_color),
            center=Point2D(entry["x"], entry["y"]),
        )
    except ValueError as exc:
        raise ConfigError(f"{where}: {exc}") from None


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be a JSON object")
    return value


def _reject_unknown(obj: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


# --- JSON text and files -------------------------------------------------------------------


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ConfigError(f"duplicate key '{key}'")
        obj[key] = value
    return obj


def _reject_constant(token: str) -> Any:
    raise ConfigError(f"invalid number {token} (numbers must be finite)")


def parse_config(text: str) -> TargetConfig:
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON: {exc}") from None
    return config_from_dict(data)


def dumps_config(config: TargetConfig) -> str:
    """Human-editable JSON: one line per target, one line per threshold."""
    data = config_to_dict(config)
    width = max(len(key) for key in data["targets"]) + 3
    target_lines = [
        f"    {json.dumps(key) + ':':<{width}} {json.dumps(value)}" for key, value in data["targets"].items()
    ]
    threshold_lines = [f"    {json.dumps(key)}: {json.dumps(value)}" for key, value in data["thresholds"].items()]
    return (
        "{\n  \"targets\": {\n" + ",\n".join(target_lines) + "\n  },\n"
        "  \"thresholds\": {\n" + ",\n".join(threshold_lines) + "\n  }\n}\n"
    )


def load_config(path: str | Path) -> TargetConfig:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from None
    try:
        return parse_config(text)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def save_config(config: TargetConfig, path: str | Path, overwrite: bool = False) -> Path:
    """Write config as JSON. Refuses to replace an existing file unless overwrite=True."""
    path = Path(path)
    try:
        with path.open("w" if overwrite else "x", encoding="utf-8") as fh:
            fh.write(dumps_config(config))
    except FileExistsError:
        raise ConfigError(f"{path} already exists (use --force to overwrite)") from None
    except OSError as exc:
        raise ConfigError(f"cannot write config {path}: {exc}") from None
    return path


def format_config(config: TargetConfig, source: str = "defaults") -> str:
    lines = [f"MORPH POINT targets ({source}); coordinates are normalized, x right / y down:"]
    for t in config.targets:
        lines.append(f"  {t.id.name:<13} x={t.center.x:.2f} y={t.center.y:.2f}  {t.label} ({t.color})")
    lines.append("thresholds:")
    for name in THRESHOLD_FIELDS:
        lines.append(f"  {name:<17} {getattr(config, name)}")
    return "\n".join(lines)
