"""Pure conversion of provider JSON into records and the desired file set."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .layout import DEFAULT_LAYOUT, Candidate, Destination
from .naming import dedupe_folders, episode_path, movie_paths, show_nfo_path, split_title

log = logging.getLogger(__name__)

DEFAULT_EXT = "mp4"


@dataclass(frozen=True)
class Movie:
    stream_id: int
    title: str
    year: int | None
    ext: str
    tmdb: str | None
    is_4k: bool = False
    category: str = ""
    category_id: int = 0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Episode:
    episode_id: int
    season: int
    number: int
    ext: str


@dataclass(frozen=True)
class Show:
    series_id: int
    title: str
    year: int | None
    tmdb: str | None
    episodes: tuple[Episode, ...]
    is_4k: bool = False
    category: str = ""
    category_id: int = 0
    tags: tuple[str, ...] = ()


def _tmdb(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.isdigit() else None


def _ext(value: Any) -> str:
    text = str(value or "").strip().lstrip(".")
    return text or DEFAULT_EXT


def _is_4k(item: dict, category_name: str) -> bool:
    """True when the provider marks the item or its category as 4K/UHD."""
    text = f"{item.get('name', '')} {category_name}".upper()
    return "4K" in text or "⁴ᴷ" in text or "2160" in text or "3840" in text or "UHD" in text


def _category_id(item: dict) -> int:
    try:
        return int(item.get("category_id") or 0)
    except (TypeError, ValueError):
        return 0


def parse_movie(item: dict, category_name: str = "") -> Movie:
    title, year, tags = split_title(str(item["name"]))
    return Movie(
        stream_id=int(item["stream_id"]),
        title=title,
        year=year,
        ext=_ext(item.get("container_extension")),
        tmdb=_tmdb(item.get("tmdb")),
        is_4k=_is_4k(item, category_name),
        category=category_name,
        category_id=_category_id(item),
        tags=tags,
    )


def _episode_groups(info: dict) -> Iterable[tuple[Any, list]]:
    raw = info.get("episodes") or {}
    if isinstance(raw, dict):
        return raw.items()
    return ((None, group) for group in raw)


def parse_show(item: dict, info: dict, category_name: str = "") -> Show:
    title, year, tags = split_title(str(item["name"]))
    series_id = item.get("series_id")
    episodes: list[Episode] = []
    for season_key, group in _episode_groups(info):
        if isinstance(group, dict):
            # Some panels return a season group as a dict keyed by episode
            # number rather than a list.
            group = group.values()
        for ep in group:
            try:
                season = int(ep.get("season") or season_key or 0)
                episodes.append(
                    Episode(
                        episode_id=int(ep["id"]),
                        season=season,
                        number=int(ep["episode_num"]),
                        ext=_ext(ep.get("container_extension")),
                    )
                )
            except (KeyError, ValueError, TypeError):
                log.warning("skipping malformed episode for series %s: %r", series_id, ep)
                continue
    episodes.sort(key=lambda e: (e.season, e.number, e.episode_id))
    return Show(
        series_id=int(item["series_id"]),
        title=title,
        year=year,
        tmdb=_tmdb(item.get("tmdb")),
        episodes=tuple(episodes),
        is_4k=_is_4k(item, category_name),
        category=category_name,
        category_id=_category_id(item),
        tags=tags,
    )


def dedupe_by_tmdb[T](items: Iterable[T], id_of: Callable[[T], int]) -> list[T]:
    """Collapse items sharing a TMDb ID, preferring 4K, then the newest entry.

    "Newest" means the highest provider ID, since providers allocate IDs in
    order of addition. Items with no TMDb ID are all kept; the folder-collision
    suffix still separates them. Each item must expose `.tmdb` (a digit string,
    or None) and `.is_4k`. The result is sorted by `id_of`.
    """
    kept_by_tmdb: dict[str, T] = {}
    untagged: list[T] = []
    for item in items:
        tmdb = item.tmdb
        if tmdb is None:
            untagged.append(item)
            continue
        current = kept_by_tmdb.get(tmdb)
        if current is None or (item.is_4k, id_of(item)) > (current.is_4k, id_of(current)):
            kept_by_tmdb[tmdb] = item
    result = [*kept_by_tmdb.values(), *untagged]
    result.sort(key=id_of)
    return result


def movie_nfo(tmdb: str) -> str:
    return f"<movie><tmdbid>{tmdb}</tmdbid></movie>\n"


def show_nfo(tmdb: str) -> str:
    return f"<tvshow><tmdbid>{tmdb}</tmdbid></tvshow>\n"


UrlFn = Callable[[int, str], str]
ResolveFn = Callable[[Candidate], Destination | None]
CollideFn = Callable[[str, str, str | None], bool]


def candidate(item: Movie | Show) -> Candidate:
    return Candidate(
        kind="movie" if isinstance(item, Movie) else "series",
        title=item.title,
        year=item.year,
        tmdb=item.tmdb,
        category=item.category,
        category_id=item.category_id,
        tags=item.tags,
        is_4k=item.is_4k,
    )


def never_collides(kind: str, folder: str, tmdb: str | None) -> bool:
    return False


@dataclass
class Built:
    """The desired file set plus what was left out and why."""

    files: dict[str, str] = field(default_factory=dict)
    libraries: set[str] = field(default_factory=set)
    skipped: int = 0
    collided: int = 0


def _place[T](
    items: list[T],
    id_of: Callable[[T], int],
    resolve: ResolveFn,
    collides: CollideFn,
    built: Built,
) -> dict[int, Destination]:
    """Resolve every item, then de-duplicate folder names within each library."""
    placed: dict[int, Destination] = {}
    for item in items:
        cand = candidate(item)  # type: ignore[arg-type]
        dest = resolve(cand)
        if dest is None:
            built.skipped += 1
            continue
        if collides(cand.kind, dest.folder, cand.tmdb):
            built.collided += 1
            continue
        placed[id_of(item)] = dest
    by_library: dict[str, list[tuple[int, str]]] = {}
    for item_id, dest in placed.items():
        by_library.setdefault(dest.library, []).append((item_id, dest.folder))
    for library, entries in by_library.items():
        built.libraries.add(library)
        for item_id, folder in dedupe_folders(entries).items():
            placed[item_id] = Destination(library, folder)
    return placed


def build_files(
    movies: Iterable[Movie],
    shows: Iterable[Show],
    movie_url: UrlFn,
    episode_url: UrlFn,
    resolve: ResolveFn = DEFAULT_LAYOUT.resolve,
    collides: CollideFn = never_collides,
) -> Built:
    """Return every file that should exist, keyed by path relative to the output root."""
    built = Built()
    files = built.files

    movies = list(movies)
    placed = _place(movies, lambda m: m.stream_id, resolve, collides, built)
    for movie in movies:
        dest = placed.get(movie.stream_id)
        if dest is None:
            continue
        strm, nfo = movie_paths(dest.library, dest.folder)
        files[strm] = movie_url(movie.stream_id, movie.ext) + "\n"
        if movie.tmdb:
            files[nfo] = movie_nfo(movie.tmdb)

    shows = [s for s in shows if s.episodes]
    placed = _place(shows, lambda s: s.series_id, resolve, collides, built)
    for show in shows:
        dest = placed.get(show.series_id)
        if dest is None:
            continue
        if show.tmdb:
            files[show_nfo_path(dest.library, dest.folder)] = show_nfo(show.tmdb)
        for ep in show.episodes:
            path = episode_path(dest.library, dest.folder, ep.season, ep.number)
            files.setdefault(path, episode_url(ep.episode_id, ep.ext) + "\n")

    return built
