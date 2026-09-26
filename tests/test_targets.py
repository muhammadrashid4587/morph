import dataclasses
import json
import math
from pathlib import Path

import pytest

from morph_pi import app
from morph_pi.models import Point2D, Target, TargetConfig, TargetId
from morph_pi.targets import (
    ConfigError,
    config_from_dict,
    config_to_dict,
    default_config,
    dumps_config,
    format_config,
    load_config,
    parse_config,
    save_config,
)

# --- defaults --------------------------------------------------------------------------


def test_default_targets_and_thresholds() -> None:
    config = default_config()
    centers = {t.id: (t.center.x, t.center.y) for t in config.targets}
    assert centers == {
        TargetId.BLUE_BLOCK: (0.20, 0.62),
        TargetId.YELLOW_BLOCK: (0.50, 0.62),
        TargetId.GREEN_BLOCK: (0.80, 0.62),
    }
    assert [t.id for t in config.targets] == list(TargetId)
    assert config.max_ray_distance == 0.12
    assert config.ambiguity_margin == 0.035
    assert config.min_confidence == 0.65
    assert config.stable_frames == 12
    assert config.target(TargetId.GREEN_BLOCK).color == "green"


# --- model validation ------------------------------------------------------------------------


def targets_with(**centers: tuple[float, float]) -> tuple[Target, ...]:
    return tuple(
        dataclasses.replace(t, center=Point2D(*centers[t.id.value])) if t.id.value in centers else t
        for t in default_config().targets
    )


def test_config_rejects_duplicate_target_ids() -> None:
    blue = default_config().target(TargetId.BLUE_BLOCK)
    with pytest.raises(ValueError, match="duplicate target id 'blue_block'"):
        TargetConfig(targets=(*default_config().targets, blue))


def test_config_rejects_missing_targets() -> None:
    with pytest.raises(ValueError, match="missing target.*green_block"):
        TargetConfig(targets=default_config().targets[:2])


def test_config_rejects_shared_centers() -> None:
    with pytest.raises(ValueError, match="share the same center"):
        TargetConfig(targets=targets_with(yellow_block=(0.2, 0.62)))


def test_config_orders_targets_canonically() -> None:
    config = TargetConfig(targets=tuple(reversed(default_config().targets)))
    assert [t.id for t in config.targets] == list(TargetId)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("max_ray_distance", 0.0, "max_ray_distance"),
        ("max_ray_distance", -0.1, "max_ray_distance"),
        ("max_ray_distance", 2.0, "max_ray_distance"),
        ("max_ray_distance", math.nan, "finite"),
        ("ambiguity_margin", -0.01, "ambiguity_margin"),
        ("ambiguity_margin", 0.12, "ambiguity_margin"),  # must be < max_ray_distance
        ("ambiguity_margin", math.inf, "finite"),
        ("min_confidence", 1.5, "min_confidence"),
        ("min_confidence", -0.1, "min_confidence"),
        ("stable_frames", 0, "stable_frames"),
        ("stable_frames", True, "stable_frames must be an integer"),
        ("stable_frames", 1.5, "stable_frames must be an integer"),
        ("stable_frames", "12", "stable_frames must be an integer"),
        ("min_ray_length", 0.0, "min_ray_length"),
        ("min_ray_length", 1.0, "min_ray_length"),
        ("closeness_weight", 1.1, "closeness_weight"),
        ("closeness_weight", "0.5", "must be a number"),
    ],
)
def test_config_rejects_invalid_thresholds(field: str, value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        TargetConfig(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"id": "blue_block"}, "TargetId"),
        ({"label": ""}, "label"),
        ({"color": "   "}, "color"),
        ({"center": (0.2, 0.62)}, "Point2D"),
    ],
)
def test_target_validation(kwargs: dict, match: str) -> None:
    base = {"id": TargetId.BLUE_BLOCK, "label": "Blue", "color": "blue", "center": Point2D(0.2, 0.62)}
    with pytest.raises(ValueError, match=match):
        Target(**{**base, **kwargs})


# --- JSON round trips ----------------------------------------------------------------------------


def test_dict_round_trip() -> None:
    config = TargetConfig(targets=targets_with(green_block=(0.9, 0.3)), stable_frames=8, min_confidence=0.7)
    assert config_from_dict(config_to_dict(config)) == config


def test_json_text_round_trip_is_concise() -> None:
    text = dumps_config(default_config())
    assert parse_config(text) == default_config()
    assert json.loads(text)["targets"]["blue_block"] == {"x": 0.2, "y": 0.62, "label": "Blue block", "color": "blue"}
    target_lines = [line for line in text.splitlines() if '"x"' in line]
    assert len(target_lines) == 3  # one line per target


def test_file_round_trip_and_overwrite_protection(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    save_config(default_config(), path)
    assert load_config(path) == default_config()
    custom = TargetConfig(targets=targets_with(blue_block=(0.1, 0.7)))
    with pytest.raises(ConfigError, match="already exists"):
        save_config(custom, path)
    assert load_config(path) == default_config()  # untouched
    save_config(custom, path, overwrite=True)
    assert load_config(path) == custom


def test_partial_config_uses_defaults() -> None:
    config = parse_config(
        '{"targets": {"blue_block": {"x": 0.1, "y": 0.6}, "yellow_block": {"x": 0.5, "y": 0.6},'
        ' "green_block": {"x": 0.9, "y": 0.6, "label": "Lime", "color": "lime"}},'
        ' "thresholds": {"stable_frames": 5}}'
    )
    assert config.stable_frames == 5
    assert config.max_ray_distance == 0.12
    assert config.target(TargetId.BLUE_BLOCK).label == "Blue block"
    assert config.target(TargetId.GREEN_BLOCK).label == "Lime"


VALID_TARGETS = '"blue_block": {"x": 0.2, "y": 0.62}, "yellow_block": {"x": 0.5, "y": 0.62}, "green_block": {"x": 0.8, "y": 0.62}'


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("{not json", "invalid JSON"),
        ("[]", "config: must be a JSON object"),
        ("{}", "missing required key 'targets'"),
        ('{"targets": []}', "targets: must be a JSON object"),
        ('{"targets": {' + VALID_TARGETS + '}, "extra": 1}', r"unknown key\(s\) \['extra'\]"),
        ('{"targets": {' + VALID_TARGETS + ', "red_block": {"x": 0.5, "y": 0.5}}}', "targets.red_block: unknown target id"),
        ('{"targets": {' + VALID_TARGETS + ', "blue_block": {"x": 0.3, "y": 0.62}}}', "duplicate key 'blue_block'"),
        ('{"targets": {"blue_block": {"x": 0.2, "y": 0.62}, "yellow_block": {"x": 0.5, "y": 0.62}}}', "missing target.*green_block"),
        ('{"targets": {"blue_block": {"x": 0.2}, "yellow_block": {"x": 0.5, "y": 0.62}, "green_block": {"x": 0.8, "y": 0.62}}}', "targets.blue_block: missing required key 'y'"),
        ('{"targets": {"blue_block": {"x": 1.5, "y": 0.62}, "yellow_block": {"x": 0.5, "y": 0.62}, "green_block": {"x": 0.8, "y": 0.62}}}', r"targets.blue_block: Point2D.x must be within"),
        ('{"targets": {"blue_block": {"x": "0.2", "y": 0.62}, "yellow_block": {"x": 0.5, "y": 0.62}, "green_block": {"x": 0.8, "y": 0.62}}}', "must be a number"),
        ('{"targets": {"blue_block": {"x": NaN, "y": 0.62}, "yellow_block": {"x": 0.5, "y": 0.62}, "green_block": {"x": 0.8, "y": 0.62}}}', "invalid number NaN"),
        ('{"targets": {"blue_block": {"x": 0.2, "y": 0.62, "z": 1}, "yellow_block": {"x": 0.5, "y": 0.62}, "green_block": {"x": 0.8, "y": 0.62}}}', "targets.blue_block: unknown key"),
        ('{"targets": {' + VALID_TARGETS + '}, "thresholds": {"max_ray_distnace": 0.2}}', "thresholds: unknown key"),
        ('{"targets": {' + VALID_TARGETS + '}, "thresholds": {"stable_frames": 0}}', "thresholds: stable_frames"),
        ('{"targets": {' + VALID_TARGETS + '}, "thresholds": []}', "thresholds: must be a JSON object"),
    ],
)
def test_parse_config_rejects_invalid_input(text: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_config(text)


def test_load_config_reports_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(missing)
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match=f"{bad.name}: config: missing required key"):
        load_config(bad)


def test_format_config_lists_targets() -> None:
    text = format_config(default_config())
    assert "BLUE_BLOCK    x=0.20 y=0.62" in text
    assert "stable_frames     12" in text


# --- CLI: targets --------------------------------------------------------------------------------


def test_cli_show_targets(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["--show-targets"]) == 0
    out = capsys.readouterr().out
    assert "(defaults)" in out
    for name in ("BLUE_BLOCK", "YELLOW_BLOCK", "GREEN_BLOCK"):
        assert name in out


def test_cli_save_then_show_custom_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "targets.json"
    assert app.main(["--save-default-targets", str(path)]) == 0
    assert load_config(path) == default_config()

    assert app.main(["--save-default-targets", str(path)]) == 2  # refuses to overwrite
    assert "already exists" in capsys.readouterr().err
    assert app.main(["--save-default-targets", str(path), "--force"]) == 0

    data = json.loads(path.read_text())
    data["targets"]["green_block"]["x"] = 0.9
    path.write_text(json.dumps(data))
    capsys.readouterr()
    assert app.main(["--config", str(path), "--show-targets"]) == 0
    out = capsys.readouterr().out
    assert str(path) in out and "GREEN_BLOCK   x=0.90" in out


def test_cli_invalid_config_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"targets": {}}')
    assert app.main(["--config", str(path), "--show-targets"]) == 2
    assert "missing target" in capsys.readouterr().err
    assert app.main(["--config", str(tmp_path / "missing.json"), "--show-targets"]) == 2


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--show-targets", "--simulate"],
        ["--show-targets", "--force"],
        ["--show-targets", "--scenario", "blue"],
        ["--save-default-targets", "x.json", "--config", "y.json"],
    ],
)
def test_cli_usage_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        app.main(argv)
    assert info.value.code == 2
