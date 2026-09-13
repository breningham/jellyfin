"""Pure helpers that turn provider titles into filesystem paths."""

from __future__ import annotations

import re
from collections.abc import Iterable

# A provider tag is one whitespace-free token of upper-case letters, digits
# and + . -, followed by " - ".  Examples: "EN - ", "4K-NF - ".
_PREFIX = re.compile(r"^([A-Z0-9+.\-]{2,12})\s+-\s+")
_COUNTRY = re.compile(r"\s*\([A-Za-z]{2,3}\)\s*$")
_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")
_WS = re.compile(r"\s+")
_UNSAFE = re.compile(r'[\\/:*?"<>|]')

MOVIES_DIR = "Movies"
SHOWS_DIR = "Shows"


def split_title(raw: str) -> tuple[str, int | None, tuple[str, ...]]:
    """Strip provider prefixes and country tags; return (title, year, tags)."""
    title = raw.strip()
    tags: list[str] = []
    for _ in range(2):
        match = _PREFIX.match(title)
        if match is None:
            break
        tags.append(match.group(1))
        title = title[match.end() :]
    title = _COUNTRY.sub("", title)
    year: int | None = None
    match = _YEAR.search(title)
    if match:
        year = int(match.group(1))
        title = title[: match.start()]
    title = _WS.sub(" ", title).strip()
    return title, year, tuple(tags)


def clean_title(raw: str) -> tuple[str, int | None]:
    """Strip provider prefixes and country tags; split off a trailing year."""
    title, year, _ = split_title(raw)
    return title, year


def safe_component(s: str) -> str:
    """Make a string safe as a single path component."""
    s = _UNSAFE.sub("-", s)
    s = _WS.sub(" ", s).strip(" .")
    return s or "untitled"


def title_folder(title: str, year: int | None) -> str:
    base = safe_component(title)
    return f"{base} ({year})" if year else base


def dedupe_folders(entries: Iterable[tuple[int, str]]) -> dict[int, str]:
    """Map provider ID -> folder name, suffixing later duplicates with the ID."""
    result: dict[int, str] = {}
    seen: set[str] = set()
    for item_id, folder in sorted(entries, key=lambda e: e[0]):
        if folder in seen:
            folder = f"{folder} [xtream-{item_id}]"
        seen.add(folder)
        result[item_id] = folder
    return result


def assign_folders(entries: Iterable[tuple[int, str, int | None]]) -> dict[int, str]:
    """Map provider ID -> folder name, suffixing later duplicates with the ID."""
    return dedupe_folders((item_id, title_folder(title, year)) for item_id, title, year in entries)


def movie_paths(library: str, folder: str) -> tuple[str, str]:
    return (
        f"{library}/{folder}/{folder}.strm",
        f"{library}/{folder}/movie.nfo",
    )


def show_nfo_path(library: str, folder: str) -> str:
    return f"{library}/{folder}/tvshow.nfo"


def episode_path(library: str, folder: str, season: int, episode: int) -> str:
    return f"{library}/{folder}/Season {season:02d}/{folder} S{season:02d}E{episode:02d}.strm"
