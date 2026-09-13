"""One complete sync pass: provider -> desired files -> disk -> Jellyfin."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Movie, Show, build_files, candidate, dedupe_by_tmdb, parse_movie, parse_show
from .categories_template import clean_name
from .client import ProviderError, XtreamClient
from .collisions import CollisionIndex
from .config import Categories, Settings
from .jellyfin import refresh_guide as jellyfin_refresh_guide
from .jellyfin import refresh_library
from .layout import DEFAULT_LAYOUT, KIND_DIRS, Layout
from .livetv import Channel, filter_xmltv, parse_channels, render_m3u
from .series_cache import SeriesCache
from .writer import Diff, apply_diff, load_manifest, plan_diff, save_manifest

log = logging.getLogger(__name__)

LIVE_M3U = "live.m3u"
GUIDE_XML = "guide.xml"


class RunAborted(Exception):
    """The run was abandoned before any file was changed."""


@dataclass
class RunSummary:
    movies: int
    shows: int
    episodes: int
    diff: Diff
    series_fetched: int
    series_cached: int
    empty_categories: list[str] = field(default_factory=list)
    refreshed: bool | None = None
    channels: int = 0
    programmes: int = 0
    guide_refreshed: bool | None = None
    skipped: int = 0
    collided: int = 0
    rules: int = 0


def _category_names(items: list[dict]) -> dict[int, str]:
    names: dict[int, str] = {}
    for item in items:
        try:
            names[int(item["category_id"])] = str(item.get("category_name", ""))
        except (KeyError, ValueError, TypeError):
            continue
    return names


def _collect_movies(
    client: XtreamClient, wanted: tuple[int, ...], known: dict[int, str], empty: list[str]
) -> dict[int, Movie]:
    movies: dict[int, Movie] = {}
    for cid in wanted:
        if cid not in known:
            log.warning("vod category %s is not offered by the provider", cid)
            empty.append(f"vod:{cid} (not offered by provider)")
            continue
        items = client.vod_streams(cid)
        if not items:
            log.warning("vod category %s %r returned nothing", cid, known[cid])
            empty.append(f"vod:{cid} {known[cid]}".rstrip())
            continue
        for item in items:
            try:
                movie = parse_movie(item, known[cid])
            except (KeyError, ValueError, TypeError, AttributeError):
                log.warning("skipping malformed movie entry: %r", item)
                continue
            movies.setdefault(movie.stream_id, movie)
    return movies


def _collect_shows(
    client: XtreamClient,
    wanted: tuple[int, ...],
    known: dict[int, str],
    cache: SeriesCache,
    empty: list[str],
) -> tuple[dict[int, Show], int, int]:
    shows: dict[int, Show] = {}
    fetched = cached = 0
    for cid in wanted:
        if cid not in known:
            log.warning("series category %s is not offered by the provider", cid)
            empty.append(f"series:{cid} (not offered by provider)")
            continue
        items = client.series(cid)
        if not items:
            log.warning("series category %s %r returned nothing", cid, known[cid])
            empty.append(f"series:{cid} {known[cid]}".rstrip())
            continue
        for item in items:
            try:
                sid = int(item["series_id"])
            except (KeyError, ValueError, TypeError, AttributeError):
                log.warning("skipping malformed series entry: %r", item)
                continue
            if sid in shows:
                continue
            last_modified = item.get("last_modified")
            info = cache.get(sid, last_modified)
            if info is not None:
                cached += 1
            else:
                try:
                    fresh_info = client.series_info(sid)
                except ProviderError as exc:
                    info = cache.get(sid, None)
                    if info is None:
                        log.warning("skipping series %s: %s", sid, exc)
                        continue
                    log.warning("using stale episode list for series %s: %s", sid, exc)
                else:
                    fetched += 1
                    try:
                        fresh_show = parse_show(item, fresh_info, known[cid])
                    except (KeyError, ValueError, TypeError, AttributeError) as exc:
                        log.warning("skipping series %s: malformed data (%s)", sid, exc)
                        continue
                    if fresh_show.episodes:
                        cache.put(sid, last_modified, fresh_info)
                        shows[sid] = fresh_show
                        continue
                    # A successful call that reports zero episodes is
                    # treated as a failure, not a result: don't overwrite
                    # a good cached episode list with an empty one.
                    log.warning(
                        "series %s returned zero episodes; not trusting it as a result", sid
                    )
                    info = cache.get(sid, None)
                    if info is None:
                        log.warning(
                            "skipping series %s: no stale episode list to fall back to", sid
                        )
                        continue
                    log.warning("using stale episode list for series %s", sid)
            try:
                shows[sid] = parse_show(item, info, known[cid])
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                log.warning("skipping series %s: malformed data (%s)", sid, exc)
    return shows, fetched, cached


def _collect_channels(
    client: XtreamClient, wanted: tuple[int, ...], known: dict[int, str], empty: list[str]
) -> list[Channel]:
    channels: list[Channel] = []
    seen: set[int] = set()
    for cid in wanted:
        if cid not in known:
            log.warning("live category %s is not offered by the provider", cid)
            empty.append(f"live:{cid} (not offered by provider)")
            continue
        parsed = parse_channels(client.live_streams(cid), clean_name(known[cid]))
        if not parsed:
            log.warning("live category %s %r returned no channels", cid, known[cid])
            empty.append(f"live:{cid} {known[cid]}".rstrip())
            continue
        for ch in parsed:
            if ch.stream_id not in seen:
                seen.add(ch.stream_id)
                channels.append(ch)
    return channels


def _build_guide(client: XtreamClient, channels: list[Channel], output_dir: Path) -> tuple[str | None, int]:
    """Filtered XMLTV text and programme count; falls back to the existing file on failure."""
    epg_ids = {ch.epg_id for ch in channels if ch.epg_id}
    try:
        guide = filter_xmltv(client.xmltv(), epg_ids)
    except (ProviderError, ET.ParseError, ValueError) as exc:
        previous = output_dir / GUIDE_XML
        try:
            text = previous.read_text(encoding="utf-8")
        except OSError:
            log.warning("guide download failed (%s); no previous guide to keep", exc)
            return None, 0
        log.warning("guide download failed (%s); keeping previous %s", exc, GUIDE_XML)
        return text, text.count("<programme ")
    log.info("guide: %d channels, %d programmes", guide.channels, guide.programmes)
    return guide.text, guide.programmes


def run_once(
    client: XtreamClient,
    settings: Settings,
    categories: Categories,
    cache: SeriesCache,
    refresh: Callable[[str, str], bool | None] = refresh_library,
    refresh_guide: Callable[[str, str], bool | None] = jellyfin_refresh_guide,
    layout: Layout = DEFAULT_LAYOUT,
) -> RunSummary:
    empty: list[str] = []

    index = CollisionIndex.build(layout.movie_dirs, layout.show_dirs)

    known_vod = _category_names(client.vod_categories()) if categories.vod else {}
    known_series = _category_names(client.series_categories()) if categories.series else {}
    known_live = _category_names(client.live_categories()) if categories.live else {}

    movies = _collect_movies(client, categories.vod, known_vod, empty)
    shows, fetched, cached = _collect_shows(client, categories.series, known_series, cache, empty)
    channels = _collect_channels(client, categories.live, known_live, empty)

    movie_list = dedupe_by_tmdb(movies.values(), lambda m: m.stream_id)
    show_list = dedupe_by_tmdb(shows.values(), lambda s: s.series_id)
    log.info(
        "deduped by tmdb: movies %d -> %d, shows %d -> %d",
        len(movies),
        len(movie_list),
        len(shows),
        len(show_list),
    )

    starved_kinds = []
    if categories.vod and not movie_list:
        starved_kinds.append("vod")
    if categories.series and not show_list:
        starved_kinds.append("series")
    if categories.live and not channels:
        starved_kinds.append("live")
    if starved_kinds:
        raise RunAborted(
            f"{'/'.join(starved_kinds)}: every selected category returned nothing; "
            "leaving files untouched"
        )

    built = build_files(
        movie_list, show_list, client.movie_url, client.episode_url, layout.resolve, index.collides
    )
    if built.skipped or built.collided:
        log.info("layout: skipped %d by rule, %d already in a real library", built.skipped, built.collided)
    desired = built.files
    programmes = 0
    if channels:
        desired[LIVE_M3U] = render_m3u(channels, client.live_url)
        guide_text, programmes = _build_guide(client, channels, settings.output_dir)
        if guide_text is not None:
            desired[GUIDE_XML] = guide_text
    old = load_manifest(settings.state_dir)
    diff = plan_diff(old, desired)
    keep = built.libraries | set(KIND_DIRS.values())
    new_manifest = apply_diff(settings.output_dir, desired, diff, keep=keep)
    save_manifest(settings.state_dir, new_manifest)

    refreshed: bool | None = None
    changed_paths = [
        *diff.added,
        *diff.updated,
        *diff.removed,
        *(dst for _, dst in diff.moved),
        *diff.restored,
    ]
    media_changed = any(p not in (LIVE_M3U, GUIDE_XML) for p in changed_paths)
    if settings.jellyfin_api_key and media_changed:
        refreshed = refresh(settings.jellyfin_url, settings.jellyfin_api_key)
    elif settings.jellyfin_api_key:
        log.info("no file changes; not requesting a jellyfin refresh")

    guide_refreshed: bool | None = None
    live_changed = any(p in (LIVE_M3U, GUIDE_XML) for p in changed_paths)
    if settings.jellyfin_api_key and live_changed:
        guide_refreshed = refresh_guide(settings.jellyfin_url, settings.jellyfin_api_key)

    return RunSummary(
        movies=len(movie_list),
        shows=len(show_list),
        episodes=sum(len(s.episodes) for s in show_list),
        diff=diff,
        series_fetched=fetched,
        series_cached=cached,
        empty_categories=empty,
        refreshed=refreshed,
        channels=len(channels),
        programmes=programmes,
        guide_refreshed=guide_refreshed,
        skipped=built.skipped,
        collided=built.collided,
        rules=len(layout.rules),
    )


def check_layout(client: XtreamClient, categories: Categories, layout: Layout, limit: int = 20) -> str:
    """Where the first `limit` titles of each enabled category would land."""
    index = CollisionIndex.build(layout.movie_dirs, layout.show_dirs)
    lines: list[str] = []

    def describe(item: Movie | Show) -> str:
        cand = candidate(item)
        rule = next((r for r in layout.rules if r.matches(cand)), None)
        dest = layout.resolve(cand)
        if dest is None:
            return f"skip (rule {rule.index})"
        if index.collides(cand.kind, dest.folder, cand.tmdb):
            return "collision (already in a real library)"
        return f"{dest.library}/{dest.folder}"

    known_vod = _category_names(client.vod_categories()) if categories.vod else {}
    for cid in categories.vod:
        name = known_vod.get(cid, "(not offered by provider)")
        lines.append(f"vod {cid} {name}")
        for item in client.vod_streams(cid)[:limit] if cid in known_vod else []:
            movie = parse_movie(item, name)
            lines.append(f"  {movie.title}{f' ({movie.year})' if movie.year else ''}  ->  {describe(movie)}")

    known_series = _category_names(client.series_categories()) if categories.series else {}
    for cid in categories.series:
        name = known_series.get(cid, "(not offered by provider)")
        lines.append(f"series {cid} {name}")
        for item in client.series(cid)[:limit] if cid in known_series else []:
            show = parse_show(item, {}, name)
            lines.append(f"  {show.title}{f' ({show.year})' if show.year else ''}  ->  {describe(show)}")
    return "\n".join(lines) + "\n"
