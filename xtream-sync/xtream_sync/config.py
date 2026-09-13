"""Settings from the environment and category selection from YAML."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_VARS = ("XTREAM_HOST", "XTREAM_USERNAME", "XTREAM_PASSWORD")
DEFAULT_INTERVAL = 21600


def parse_hosts(raw: str) -> tuple[str, ...]:
    """Split a comma-separated host list, trimming whitespace and trailing slashes."""
    return tuple(h.strip().rstrip("/") for h in raw.split(",") if h.strip())


class ConfigError(Exception):
    """Configuration is missing or invalid; the caller should exit or skip."""


@dataclass(frozen=True)
class Settings:
    hosts: tuple[str, ...]
    username: str
    password: str
    jellyfin_url: str
    jellyfin_api_key: str | None
    interval: int
    output_dir: Path
    state_dir: Path
    categories_path: Path
    layout_path: Path


def load_settings(env: Mapping[str, str]) -> Settings:
    missing = [name for name in REQUIRED_VARS if not env.get(name)]
    if missing:
        raise ConfigError(f"missing required environment variables: {', '.join(missing)}")

    raw_interval = env.get("XTREAM_SYNC_INTERVAL", str(DEFAULT_INTERVAL))
    try:
        interval = int(raw_interval)
    except ValueError as exc:
        raise ConfigError(
            f"XTREAM_SYNC_INTERVAL must be an integer number of seconds, got {raw_interval!r}"
        ) from exc
    if interval <= 0:
        raise ConfigError(f"XTREAM_SYNC_INTERVAL must be positive, got {interval}")

    hosts = parse_hosts(env["XTREAM_HOST"])
    if not hosts:
        raise ConfigError("XTREAM_HOST must contain at least one base URL")

    return Settings(
        hosts=hosts,
        username=env["XTREAM_USERNAME"],
        password=env["XTREAM_PASSWORD"],
        jellyfin_url=env.get("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/"),
        jellyfin_api_key=env.get("JELLYFIN_API_KEY") or None,
        interval=interval,
        output_dir=Path(env.get("XTREAM_OUTPUT_DIR", "/output")),
        state_dir=Path(env.get("XTREAM_STATE_DIR", "/state")),
        categories_path=Path(env.get("XTREAM_CATEGORIES", "/config/categories.yaml")),
        layout_path=Path(env.get("XTREAM_LAYOUT", "/config/layout.yaml")),
    )


@dataclass(frozen=True)
class Categories:
    vod: tuple[int, ...]
    series: tuple[int, ...]
    live: tuple[int, ...] = ()


def load_categories(path: Path) -> Categories:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping with 'vod', 'series' and 'live' lists")

    def ids(key: str) -> tuple[int, ...]:
        value = data.get(key) or []
        if not isinstance(value, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in value
        ):
            raise ConfigError(f"{path}: '{key}' must be a list of integer category IDs")
        return tuple(value)

    categories = Categories(vod=ids("vod"), series=ids("series"), live=ids("live"))
    if not (categories.vod or categories.series or categories.live):
        raise ConfigError(f"{path}: no categories selected")
    return categories
