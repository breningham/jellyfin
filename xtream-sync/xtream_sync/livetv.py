"""Live TV: provider channels -> Channel records, M3U playlist, filtered XMLTV."""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .categories_template import _is_decoration

log = logging.getLogger(__name__)

_COUNTRY_PREFIX = re.compile(r"^[A-Z]{2,3}\s*[:|]\s*")
_QUALITY_TOKENS = {"HEVC", "HD", "FHD", "UHD", "SD", "4K", "8K", "ᴴᴰ", "◉", "⁴ᴷ"}


@dataclass(frozen=True)
class Channel:
    stream_id: int
    name: str
    epg_id: str | None
    logo: str | None
    group: str


def clean_channel_name(raw: str) -> str:
    """Strip country prefix and quality/decoration tokens; never return empty."""
    text = _COUNTRY_PREFIX.sub("", raw.strip())
    words = []
    for word in text.split():
        if word in _QUALITY_TOKENS:
            continue
        word = "".join(ch for ch in word if not _is_decoration(ch) and ch != "◉")
        if word:
            words.append(word)
    cleaned = " ".join(words).strip()
    return cleaned or raw.strip()


def parse_channels(items: Iterable[dict], group: str) -> list[Channel]:
    channels: list[Channel] = []
    for item in items:
        try:
            raw_name = str(item["name"])
            if raw_name.lstrip().startswith("#"):
                continue  # provider header rows like "### UK NEWS ###"
            channels.append(
                Channel(
                    stream_id=int(item["stream_id"]),
                    name=clean_channel_name(raw_name),
                    epg_id=str(item.get("epg_channel_id") or "").strip() or None,
                    logo=str(item.get("stream_icon") or "").strip() or None,
                    group=group,
                )
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            log.warning("skipping malformed live channel entry: %r", item)
    return channels


def _attr(value: str) -> str:
    return value.replace('"', "'")


def render_m3u(channels: list[Channel], url_for: Callable[[int], str]) -> str:
    lines = ["#EXTM3U"]
    for number, ch in enumerate(channels, start=1):
        attrs = []
        if ch.epg_id:
            attrs.append(f'tvg-id="{_attr(ch.epg_id)}"')
        attrs.append(f'tvg-chno="{number}"')
        attrs.append(f'tvg-name="{_attr(ch.name)}"')
        if ch.logo:
            attrs.append(f'tvg-logo="{_attr(ch.logo)}"')
        attrs.append(f'group-title="{_attr(ch.group)}"')
        # A comma in the display-name segment (after the attrs comma) would
        # read as an extra field to naive M3U parsers; tvg-name above keeps
        # the untouched name.
        display_name = ch.name.replace(", ", " ").replace(",", " ")
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{display_name}")
        lines.append(url_for(ch.stream_id))
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class FilteredGuide:
    text: str
    channels: int
    programmes: int


def filter_xmltv(xml: str | bytes, epg_ids: set[str]) -> FilteredGuide:
    """Keep only <channel> and <programme> elements for `epg_ids`; stream-parse.

    `xml` is passed to `ET.iterparse` as bytes so expat honours the
    document's own declared encoding rather than us assuming UTF-8.
    """
    kept_root: ET.Element | None = None
    kept_channels: list[ET.Element] = []
    kept_programmes: list[ET.Element] = []
    seen_ids: set[str] = set()
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    source = io.BytesIO(data)
    for event, el in ET.iterparse(source, events=("start", "end")):
        if event == "start":
            if kept_root is None and el.tag == "tv":
                kept_root = ET.Element("tv", dict(el.attrib))
            continue
        if el.tag == "channel":
            channel_id = el.get("id")
            if channel_id in epg_ids and channel_id not in seen_ids:
                seen_ids.add(channel_id)
                kept_channels.append(el)
            else:
                el.clear()
        elif el.tag == "programme":
            if el.get("channel") in epg_ids:
                kept_programmes.append(el)
            else:
                el.clear()
    if kept_root is None:
        raise ET.ParseError("no <tv> root element")
    for el in kept_channels + kept_programmes:
        kept_root.append(el)
    body = ET.tostring(kept_root, encoding="unicode")
    text = '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"
    return FilteredGuide(text=text, channels=len(kept_channels), programmes=len(kept_programmes))
