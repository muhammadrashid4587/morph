"""Settings and secrets from the repo's .env file ONLY (never the process environment).

Values are never printed, logged or put in error messages: errors name the key
and the file, not the value.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


class MissingKeyError(RuntimeError):
    """A required key is not in the .env file."""


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=VALUE lines. Supports comments, blank lines, `export KEY=...` and quoted values."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()  # inline comment after an unquoted value
        if key:
            values[key] = value
    return values


def get_secret(name: str, path: Path = ENV_FILE) -> str:
    value = read_env_file(path).get(name, "")
    if not value:
        raise MissingKeyError(f"{name} is missing from {path}. Add a line '{name}=...' to that file (it is git-ignored).")
    return value


def get_setting(name: str, default: str = "", path: Path = ENV_FILE) -> str:
    """A non-secret setting (voice id, model name, device) from .env, or the default."""
    return read_env_file(path).get(name, "") or default
