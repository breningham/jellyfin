"""Titles already present in the real libraries, so the sidecar skips them."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .naming import safe_component

log = logging.getLogger(__name__)

MAX_NFO_BYTES = 64 * 1024
NFO_NAMES = {"movie": "movie.nfo", "series": "tvshow.nfo"}
_TMDB = re.compile(
    r"<tmdbid>\s*(\d+)\s*</tmdbid>|<uniqueid[^>]*type=\"tmdb\"[^>]*>\s*(\d+)\s*</uniqueid>"
)


def _normalise(name: str) -> str:
    return safe_component(name).lower()


def _read_tmdb(nfo: Path) -> str | None:
    try:
        if nfo.stat().st_size > MAX_NFO_BYTES:
            return None
        text = nfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _TMDB.search(text)
    return (match.group(1) or match.group(2)) if match else None


@dataclass
class CollisionIndex:
    names: dict[str, set[str]] = field(default_factory=lambda: {"movie": set(), "series": set()})
    tmdb: dict[str, set[str]] = field(default_factory=lambda: {"movie": set(), "series": set()})

    @classmethod
    def build(cls, movie_dirs: Iterable[Path], show_dirs: Iterable[Path]) -> CollisionIndex:
        """One directory listing per real library folder, plus one .nfo read per title folder."""
        index = cls()
        for kind, dirs in (("movie", movie_dirs), ("series", show_dirs)):
            for directory in dirs:
                index._scan(kind, Path(directory))
        return index

    def _scan(self, kind: str, directory: Path) -> None:
        try:
            entries = list(os.scandir(directory))
        except FileNotFoundError:
            log.warning("collision directory %s does not exist; nothing to skip there", directory)
            return
        except OSError as exc:
            log.warning("cannot list collision directory %s: %s", directory, exc)
            return
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            self.names[kind].add(_normalise(entry.name))
            tmdb = _read_tmdb(Path(entry.path) / NFO_NAMES[kind])
            if tmdb:
                self.tmdb[kind].add(tmdb)

    def collides(self, kind: str, folder: str, tmdb: str | None) -> bool:
        if _normalise(folder) in self.names[kind]:
            return True
        return tmdb is not None and tmdb in self.tmdb[kind]


EMPTY_INDEX = CollisionIndex()
