"""Rules that decide where each title goes, loaded from layout.yaml."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError
from .naming import safe_component

KIND_DIRS = {"movie": "Movies", "series": "Shows"}
VARIABLES = frozenset({"title", "year", "tmdb", "kind", "category", "letter", "decade"})

_VAR = re.compile(r"\{([^{}]*)\}")
_EMPTY_BRACKETS = re.compile(r"\s*(\(\s*\)|\[\s*\]|\{\s*\})")


class TemplateError(ValueError):
    """A template names a variable that does not exist."""


@dataclass(frozen=True)
class Candidate:
    """The facts about one movie or show that rules and templates can use."""

    kind: str  # "movie" or "series"
    title: str
    year: int | None
    tmdb: str | None
    category: str
    category_id: int = 0
    tags: tuple[str, ...] = ()
    is_4k: bool = False

    def variables(self) -> dict[str, str]:
        first = self.title[:1].upper()
        return {
            "title": safe_component(self.title),
            "year": str(self.year) if self.year else "",
            "tmdb": self.tmdb or "",
            "kind": KIND_DIRS[self.kind],
            "category": safe_component(self.category) if self.category.strip() else "",
            "letter": first if first.isalpha() else "#",
            "decade": f"{self.year // 10 * 10}s" if self.year else "",
        }


def render(template: str, variables: Mapping[str, str]) -> str:
    """Substitute {variables}, drop empty brackets, make every path component safe."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise TemplateError(f"unknown template variable {{{name}}}")
        return variables[name]

    text = _VAR.sub(substitute, template)
    text = _EMPTY_BRACKETS.sub("", text)
    parts = [safe_component(part) for part in text.split("/") if part.strip(" .")]
    return "/".join(parts)


DEFAULT_INTO = "{kind}"
DEFAULT_FOLDER = "{title} ({year})"
_PROBE = dict.fromkeys(VARIABLES, "x")
_MATCH_KEYS = ("kind", "category", "title", "tag", "year_min", "year_max", "is_4k")
_RULE_KEYS = ("match", "into", "folder", "skip")


@dataclass(frozen=True)
class Destination:
    library: str
    folder: str


@dataclass(frozen=True)
class Rule:
    index: int
    kind: str | None = None
    category_re: re.Pattern[str] | None = None
    category_ids: frozenset[int] | None = None
    title_re: re.Pattern[str] | None = None
    tag_re: re.Pattern[str] | None = None
    year_min: int | None = None
    year_max: int | None = None
    is_4k: bool | None = None
    into: str = DEFAULT_INTO
    folder: str = DEFAULT_FOLDER
    skip: bool = False

    def matches(self, c: Candidate) -> bool:
        if self.kind is not None and c.kind != self.kind:
            return False
        if self.category_re is not None and not self.category_re.search(c.category):
            return False
        if self.category_ids is not None and c.category_id not in self.category_ids:
            return False
        if self.title_re is not None and not self.title_re.search(c.title):
            return False
        if self.tag_re is not None and not any(self.tag_re.search(t) for t in c.tags):
            return False
        if self.year_min is not None and (c.year is None or c.year < self.year_min):
            return False
        if self.year_max is not None and (c.year is None or c.year > self.year_max):
            return False
        if self.is_4k is not None and c.is_4k != self.is_4k:
            return False
        return True


@dataclass(frozen=True)
class Layout:
    rules: tuple[Rule, ...] = ()
    movie_dirs: tuple[Path, ...] = ()
    show_dirs: tuple[Path, ...] = ()

    def resolve(self, c: Candidate) -> Destination | None:
        """First matching rule decides; None means skip the title."""
        rule = next((r for r in self.rules if r.matches(c)), None)
        if rule is not None and rule.skip:
            return None
        into, folder = (rule.into, rule.folder) if rule else (DEFAULT_INTO, DEFAULT_FOLDER)
        variables = c.variables()
        library = render(into, variables) or KIND_DIRS[c.kind]
        return Destination(library, render(folder, variables))


DEFAULT_LAYOUT = Layout()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _regex(path: Path, index: int, key: str, value: Any) -> re.Pattern[str]:
    if not isinstance(value, str):
        raise ConfigError(f"{path}: rule {index} '{key}': must be a regex string")
    try:
        return re.compile(value, re.IGNORECASE)
    except re.error as exc:
        raise ConfigError(f"{path}: rule {index} '{key}': invalid regex: {exc}") from exc


def _template(path: Path, index: int, key: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: rule {index} '{key}': must be a non-empty string")
    if key == "folder" and "/" in value:
        raise ConfigError(f"{path}: rule {index} '{key}': must not contain /")
    try:
        render(value, _PROBE)
    except TemplateError as exc:
        raise ConfigError(f"{path}: rule {index} '{key}': {exc}") from exc
    return value


def _parse_rule(path: Path, index: int, raw: Any) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: rule {index}: expected a mapping")
    unknown = sorted(set(raw) - set(_RULE_KEYS))
    if unknown:
        raise ConfigError(f"{path}: rule {index}: unknown keys: {', '.join(unknown)}")
    if not any(k in raw for k in ("into", "folder", "skip")):
        raise ConfigError(f"{path}: rule {index}: needs at least one of into, folder, skip")
    match = raw.get("match")
    if match is None:
        match = {}
    if not isinstance(match, dict):
        raise ConfigError(f"{path}: rule {index} 'match': expected a mapping")
    unknown = sorted(set(match) - set(_MATCH_KEYS))
    if unknown:
        raise ConfigError(f"{path}: rule {index} 'match': unknown keys: {', '.join(unknown)}")

    fields: dict[str, Any] = {"index": index}
    if "kind" in match:
        if match["kind"] not in KIND_DIRS:
            raise ConfigError(f"{path}: rule {index} 'kind': must be movie or series")
        fields["kind"] = match["kind"]
    if "category" in match:
        value = match["category"]
        if isinstance(value, list) and all(_is_int(v) for v in value):
            fields["category_ids"] = frozenset(value)
        elif isinstance(value, str):
            fields["category_re"] = _regex(path, index, "category", value)
        else:
            raise ConfigError(
                f"{path}: rule {index} 'category': must be a regex string or a list of integer ids"
            )
    if "title" in match:
        fields["title_re"] = _regex(path, index, "title", match["title"])
    if "tag" in match:
        fields["tag_re"] = _regex(path, index, "tag", match["tag"])
    for key in ("year_min", "year_max"):
        if key in match:
            if not _is_int(match[key]):
                raise ConfigError(f"{path}: rule {index} '{key}': must be an integer")
            fields[key] = match[key]
    if "is_4k" in match:
        if not isinstance(match["is_4k"], bool):
            raise ConfigError(f"{path}: rule {index} 'is_4k': must be true or false")
        fields["is_4k"] = match["is_4k"]

    if "skip" in raw:
        if not isinstance(raw["skip"], bool):
            raise ConfigError(f"{path}: rule {index} 'skip': must be true or false")
        fields["skip"] = raw["skip"]
    if "into" in raw:
        fields["into"] = _template(path, index, "into", raw["into"])
    if "folder" in raw:
        fields["folder"] = _template(path, index, "folder", raw["folder"])
    return Rule(**fields)


def _parse_dirs(path: Path, collisions: dict, key: str) -> tuple[Path, ...]:
    value = collisions.get(key)
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{path}: 'collisions.{key}' must be a list of paths")
    return tuple(Path(v) for v in value)


def load_layout(path: Path) -> Layout:
    """Parse layout.yaml; a missing or empty file means the default layout."""
    if not path.exists():
        return DEFAULT_LAYOUT
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return DEFAULT_LAYOUT
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping with 'rules' and/or 'collisions'")
    unknown = sorted(set(data) - {"rules", "collisions"})
    if unknown:
        raise ConfigError(f"{path}: unknown top-level keys: {', '.join(unknown)}")

    raw_rules = data.get("rules")
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise ConfigError(f"{path}: 'rules' must be a list")
    rules = tuple(_parse_rule(path, i, raw) for i, raw in enumerate(raw_rules))

    collisions = data.get("collisions")
    if collisions is None:
        collisions = {}
    if not isinstance(collisions, dict):
        raise ConfigError(f"{path}: 'collisions' must be a mapping")
    unknown = sorted(set(collisions) - {"movies", "shows"})
    if unknown:
        raise ConfigError(f"{path}: 'collisions': unknown keys: {', '.join(unknown)}")
    return Layout(
        rules=rules,
        movie_dirs=_parse_dirs(path, collisions, "movies"),
        show_dirs=_parse_dirs(path, collisions, "shows"),
    )
