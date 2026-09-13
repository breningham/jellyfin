import xml.etree.ElementTree as ET

import pytest

from xtream_sync.livetv import (
    Channel,
    clean_channel_name,
    filter_xmltv,
    parse_channels,
    render_m3u,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("UK: BBC 1 HEVC HD", "BBC 1"),
        ("UK| SKY NEWS HD", "SKY NEWS"),
        ("UK: BEIN SP⚽RTS 1 ENGLISH ᴴᴰ ◉", "BEIN SP⚽RTS 1 ENGLISH"),
        ("UK: BBC ONE LONDON 4K ◉", "BBC ONE LONDON"),
        ("UK: HISTORY 2 HEVC HD", "HISTORY 2"),
        ("Channel 4", "Channel 4"),
        ("HD", "HD"),  # cleaning to empty keeps the original
    ],
)
def test_clean_channel_name(raw, expected):
    assert clean_channel_name(raw) == expected


RAW = [
    {"name": "### UK GENERAL HEVC/HD ###", "stream_id": 1, "epg_channel_id": "", "stream_icon": "x"},
    {"name": "UK: BBC 1 HEVC HD", "stream_id": 2, "epg_channel_id": "BBC1.uk", "stream_icon": "http://l/bbc1.png"},
    {"name": "UK: NO EPG HD", "stream_id": 3, "epg_channel_id": "", "stream_icon": ""},
    {"name": "broken", "stream_id": "abc"},
]


def test_parse_channels_drops_headers_and_malformed(caplog):
    with caplog.at_level("WARNING"):
        channels = parse_channels(RAW, "UK| GENERAL")
    assert channels == [
        Channel(2, "BBC 1", "BBC1.uk", "http://l/bbc1.png", "UK| GENERAL"),
        Channel(3, "NO EPG", None, None, "UK| GENERAL"),
    ]
    assert "broken" in caplog.text


def test_render_m3u():
    channels = [
        Channel(2, "BBC 1", "BBC1.uk", "http://l/bbc1.png", "UK| GENERAL"),
        Channel(3, 'Say "Hi"', None, None, "UK| NEWS"),
    ]
    text = render_m3u(channels, lambda sid: f"http://h/live/u/p/{sid}.ts")
    assert text.splitlines() == [
        "#EXTM3U",
        '#EXTINF:-1 tvg-id="BBC1.uk" tvg-chno="1" tvg-name="BBC 1" tvg-logo="http://l/bbc1.png" group-title="UK| GENERAL",BBC 1',
        "http://h/live/u/p/2.ts",
        "#EXTINF:-1 tvg-chno=\"2\" tvg-name=\"Say 'Hi'\" group-title=\"UK| NEWS\",Say \"Hi\"",
        "http://h/live/u/p/3.ts",
    ]
    assert text.endswith("\n")


def test_render_m3u_display_name_comma_replaced_but_tvg_name_kept():
    # A comma in the display-name segment (after the EXTINF attrs comma)
    # would otherwise look like an extra field to naive M3U parsers.
    channels = [Channel(4, "News, Sport", None, None, "UK| NEWS")]
    text = render_m3u(channels, lambda sid: f"http://h/{sid}.ts")
    lines = text.splitlines()
    assert lines[1].endswith(",News Sport")
    assert 'tvg-name="News, Sport"' in lines[1]


XMLTV = """<?xml version="1.0" encoding="utf-8"?><!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="8K">
<channel id="BBC1.uk"><display-name>UK: BBC 1</display-name><icon src="http://l/1.png" /></channel>
<channel id="AMC.hu"><display-name>HU: AMC</display-name></channel>
<channel id="SkyNews.uk"><display-name>Sky News</display-name></channel>
<programme start="20260910200000 +0100" stop="20260910210000 +0100" channel="BBC1.uk"><title lang="en">News</title><desc>Tonight's news.</desc></programme>
<programme start="20260910200000 +0100" stop="20260910210000 +0100" channel="AMC.hu"><title>Film</title></programme>
<programme start="20260910210000 +0100" stop="20260910220000 +0100" channel="BBC1.uk"><title>Drama</title></programme>
<programme start="20260910200000 +0100" stop="20260910230000 +0100" channel="SkyNews.uk"><title>Live</title></programme>
</tv>"""


def test_filter_xmltv_keeps_only_selected_channels_and_programmes():
    result = filter_xmltv(XMLTV, {"BBC1.uk", "Missing.uk"})
    assert (result.channels, result.programmes) == (1, 2)
    root = ET.fromstring(result.text)
    assert root.tag == "tv" and root.get("generator-info-name") == "8K"
    assert [c.get("id") for c in root.findall("channel")] == ["BBC1.uk"]
    progs = root.findall("programme")
    assert [p.get("channel") for p in progs] == ["BBC1.uk", "BBC1.uk"]
    assert progs[0].find("desc").text == "Tonight's news."
    assert result.text.startswith('<?xml version="1.0" encoding="utf-8"?>')


def test_filter_xmltv_empty_selection_gives_empty_tv():
    result = filter_xmltv(XMLTV, set())
    assert (result.channels, result.programmes) == (0, 0)
    assert ET.fromstring(result.text).tag == "tv"


DUPLICATE_XMLTV = """<?xml version="1.0" encoding="utf-8"?>
<tv generator-info-name="t">
<channel id="BBC1.uk"><display-name>BBC 1</display-name></channel>
<channel id="BBC1.uk"><display-name>BBC One (dup)</display-name></channel>
<programme start="20260910200000 +0100" stop="20260910210000 +0100" channel="BBC1.uk"><title>News</title></programme>
</tv>"""


def test_filter_xmltv_dedupes_channel_elements_by_id():
    # I3: the provider XMLTV sometimes lists the same channel id twice;
    # Jellyfin's XMLTV parser is unhappy with duplicate <channel> ids, so
    # keep only the first one.
    result = filter_xmltv(DUPLICATE_XMLTV, {"BBC1.uk"})
    assert result.channels == 1
    assert result.programmes == 1
    root = ET.fromstring(result.text)
    channel_els = root.findall("channel")
    assert len(channel_els) == 1
    assert channel_els[0].find("display-name").text == "BBC 1"
    assert len(root.findall("programme")) == 1


def test_filter_xmltv_bad_xml_raises():
    with pytest.raises(ET.ParseError):
        filter_xmltv("<tv><channel id='x'>", {"x"})


LATIN1_XMLTV = (
    '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
    '<tv generator-info-name="t">'
    '<channel id="FR1.fr"><display-name>Cin\xe9ma</display-name></channel>'
    "</tv>"
).encode("latin-1")


def test_filter_xmltv_accepts_bytes_and_honours_declared_encoding():
    # I4: the real XMLTV document may declare a non-UTF-8 encoding; feeding
    # ET.iterparse raw bytes (instead of a str we would have to re-encode
    # as UTF-8, corrupting non-UTF-8 documents) lets expat honour it.
    result = filter_xmltv(LATIN1_XMLTV, {"FR1.fr"})
    root = ET.fromstring(result.text)
    assert root.find("channel/display-name").text == "Cinéma"
