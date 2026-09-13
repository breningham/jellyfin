"""Render categories.yaml listing every provider category, enabled ones uncommented."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from .client import XtreamClient
from .config import Categories

HEADER = """\
# Xtream category IDs to expose as Jellyfin libraries.
# Every category the provider offered on {today} is listed; uncomment a line to
# enable it. Edits are picked up on the next sync run (or `make xtream-sync-once`).
# Titles present in several enabled categories are collapsed by TMDb ID, preferring
# a 4K entry, then the newest one, so enabling both a 4K category and its HD twin
# keeps the 4K copy and only adds titles the 4K category lacks.
#
# `live:` categories become Jellyfin Live TV channels (live.m3u + guide.xml).
# Refresh this list, keeping the current selection:  make xtream-categories
"""


def _is_decoration(ch: str) -> bool:
    name = unicodedata.name(ch, "")
    return unicodedata.category(ch) == "Lm" or any(
        word in name for word in ("SUPERSCRIPT", "SUBSCRIPT", "MODIFIER")
    )


def clean_name(raw: str) -> str:
    """Drop superscript decorations, keeping a plain [4K]/[Dolby] tag if implied."""
    tags = []
    if "⁴" in raw or "4K" in raw.upper():
        tags.append("4K")
    if "ᴰᵒˡᵇʸ" in raw or "DOLBY" in raw.upper():
        tags.append("Dolby")
    plain = "".join(ch for ch in raw if not _is_decoration(ch))
    plain = re.sub(r"\s+", " ", plain).strip(" -")
    return plain + (f"  [{' '.join(tags)}]" if tags else "")


def render(client: XtreamClient, current: Categories, today: date | None = None) -> str:
    today = today or date.today()  # noqa: DTZ011 - a cosmetic date stamp in a comment
    lines = [HEADER.format(today=today.isoformat())]
    for kind, fetch, enabled in (
        ("vod", client.vod_categories, set(current.vod)),
        ("series", client.series_categories, set(current.series)),
        ("live", client.live_categories, set(current.live)),
    ):
        lines.append(f"{kind}:")
        for item in fetch():
            cid = int(item["category_id"])
            prefix = "  - " if cid in enabled else "  #- "
            lines.append(f"{prefix}{cid:<5} # {clean_name(str(item.get('category_name', '')))}")
        lines.append("")
    return "\n".join(lines)
