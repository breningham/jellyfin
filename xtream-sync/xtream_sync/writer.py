"""Manifest-tracked writing of the desired file set to the output directory."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_manifest(state_dir: Path) -> dict[str, str]:
    path = state_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("manifest %s is not valid JSON, treating as empty: %s", path, exc)
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        log.warning("manifest %s is not a mapping of str to str, treating as empty", path)
        return {}
    return data


def save_manifest(state_dir: Path, manifest: dict[str, str]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    os.replace(tmp, state_dir / MANIFEST_NAME)


@dataclass
class Diff:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)


def plan_diff(old: dict[str, str], desired: dict[str, str]) -> Diff:
    diff = Diff()
    for path, text in desired.items():
        if path not in old:
            diff.added.append(path)
        elif old[path] != content_hash(text):
            diff.updated.append(path)
    diff.removed = [path for path in old if path not in desired]
    diff.added.sort()
    diff.updated.sort()
    diff.removed.sort()

    # A removed path and an added path with the same content are the same
    # title relocated by a rule change: rename it instead of delete + create
    # so its mtime survives and Jellyfin does not refetch its metadata.
    removed_by_hash: dict[str, list[str]] = {}
    for path in diff.removed:
        removed_by_hash.setdefault(old[path], []).append(path)
    still_added: list[str] = []
    for path in diff.added:
        sources = removed_by_hash.get(content_hash(desired[path]))
        if sources:
            diff.moved.append((sources.pop(0), path))
        else:
            still_added.append(path)
    moved_sources = {src for src, _ in diff.moved}
    diff.added = still_added
    diff.removed = [path for path in diff.removed if path not in moved_sources]
    return diff


def apply_diff(
    output_dir: Path, desired: dict[str, str], diff: Diff, keep: Iterable[str] = ()
) -> dict[str, str]:
    """Write/delete/move per `diff`; also rewrite tracked files missing on disk.

    Paths written only because they were tracked but missing on disk (e.g.
    the manifest survived but the output volume was emptied) are appended to
    `diff.restored`.

    Directories listed in `keep` (relative to output_dir) are library roots
    Jellyfin points at, and are never pruned even when empty; they are
    created if they don't already exist, even when nothing was written into
    them this run.
    """
    keep_dirs = {output_dir / rel for rel in keep}
    added_set = set(diff.added)
    updated_set = set(diff.updated)
    moved_dsts = {dst for _, dst in diff.moved}
    written = {}

    for src, dst in diff.moved:
        source = output_dir / src
        target = output_dir / dst
        text = desired[dst]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(dst)
            os.replace(source, target)
            written[dst] = content_hash(text)
        except FileExistsError:
            # The destination already exists on disk (was never pruned, or
            # someone else put a file there). Mirror the "never overwrite an
            # untracked file" rule below: adopt it if its content already
            # matches, otherwise leave it alone. Either way the source is a
            # tracked file we no longer want, so it is removed.
            try:
                existing_text = target.read_text(encoding="utf-8")
            except (OSError, ValueError) as exc:
                log.warning("not overwriting untracked file %s (%s)", dst, exc)
            else:
                if existing_text == text:
                    log.debug("adopting existing identical file %s", dst)
                    written[dst] = content_hash(text)
                else:
                    log.warning("not overwriting untracked file %s", dst)
            try:
                source.unlink()
            except FileNotFoundError:
                pass
        except OSError as exc:
            log.warning("could not move %s -> %s (%s); writing it instead", src, dst, exc)
            target.write_text(text, encoding="utf-8")
            written[dst] = content_hash(text)
            try:
                source.unlink()
            except FileNotFoundError:
                pass
        _prune_empty_parents(source.parent, output_dir, keep_dirs)

    for rel, text in desired.items():
        if rel in written or rel in moved_dsts:
            continue
        target = output_dir / rel

        if rel in added_set and target.exists():
            # A file already exists at a path we didn't previously track.
            # If its content matches what we'd write, it's ours (a lost
            # manifest, or a partial earlier write) - adopt it as-is rather
            # than rewriting it. Otherwise it's someone else's file: leave
            # it alone and exclude it from the manifest.
            try:
                existing_text = target.read_text(encoding="utf-8")
            except (OSError, ValueError) as exc:
                log.warning("not overwriting untracked file %s (%s)", rel, exc)
                continue
            if existing_text == text:
                log.debug("adopting existing identical file %s", rel)
                written[rel] = content_hash(text)
            else:
                log.warning("not overwriting untracked file %s", rel)
            continue

        if rel in updated_set or not target.exists():
            # Write updated files or tracked files missing on disk (the
            # latter is a "restored" file: tracked in the manifest but not
            # present on disk, e.g. an empty output volume with a retained
            # manifest). When replacing an existing file, put its
            # modification time back: Jellyfin treats a newer mtime as "file
            # changed" and runs a full metadata refresh, but it only reads
            # STRM/M3U contents at play time, so a URL-only rewrite must
            # stay invisible to it.
            if rel not in added_set and rel not in updated_set and not target.exists():
                diff.restored.append(rel)
            previous = target.stat() if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            if previous is not None:
                os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            written[rel] = content_hash(text)
        else:
            # Unchanged tracked file, already correct on disk
            written[rel] = content_hash(text)

    for rel in diff.removed:
        target = output_dir / rel
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        _prune_empty_parents(target.parent, output_dir, keep_dirs)

    for directory in keep_dirs:
        directory.mkdir(parents=True, exist_ok=True)

    diff.restored.sort()
    return written


def _prune_empty_parents(directory: Path, stop: Path, keep: set[Path] = frozenset()) -> None:
    while directory != stop and directory.is_relative_to(stop) and directory not in keep:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent
