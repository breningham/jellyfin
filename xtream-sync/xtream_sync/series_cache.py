"""On-disk cache of `get_series_info` results, keyed by the series' last_modified."""

from __future__ import annotations

import json
import os
from pathlib import Path


class SeriesCache:
    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "series"

    def _path(self, series_id: int) -> Path:
        return self._dir / f"{series_id}.json"

    def get(self, series_id: int, last_modified: str | int | None) -> dict | None:
        path = self._path(series_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if last_modified is not None and data.get("last_modified") != str(last_modified):
            return None
        info = data.get("info")
        return info if isinstance(info, dict) else None

    def put(self, series_id: int, last_modified: str | int | None, info: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_modified": None if last_modified is None else str(last_modified),
            "info": info,
        }
        tmp = self._path(series_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self._path(series_id))
