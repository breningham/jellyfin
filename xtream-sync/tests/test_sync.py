import dataclasses
import shutil

import pytest

from tests.fixtures import SERIES_INFO, SERIES_ITEM, VOD_ITEM
from xtream_sync.client import ProviderError
from xtream_sync.config import Categories, Settings
from xtream_sync.layout import load_layout
from xtream_sync.series_cache import SeriesCache
from xtream_sync.sync import RunAborted, _category_names, run_once
from xtream_sync.writer import load_manifest

XMLTV = """<?xml version="1.0" encoding="utf-8"?>
<tv generator-info-name="t">
<channel id="BBC1.uk"><display-name>BBC 1</display-name></channel>
<channel id="ITV1.uk"><display-name>ITV 1</display-name></channel>
<channel id="Other.hu"><display-name>Other</display-name></channel>
<programme start="20260910200000 +0100" stop="20260910210000 +0100" channel="BBC1.uk"><title>News</title></programme>
<programme start="20260910200000 +0100" stop="20260910210000 +0100" channel="Other.hu"><title>X</title></programme>
</tv>"""

LIVE = Categories(vod=(), series=(), live=(1141,))


class FakeClient:
    def __init__(self):
        self.vod_cats = [{"category_id": "119", "category_name": "EN - 2020 & OLD"}]
        self.series_cats = [{"category_id": "427", "category_name": "NETFLIX SERIES"}]
        self.vod = {119: [VOD_ITEM]}
        self.series_lists = {427: [SERIES_ITEM]}
        self.infos = {50012: SERIES_INFO}
        self.info_calls = 0
        self.fail_lists = False
        self.fail_info = False
        self.live_cats = [{"category_id": "1141", "category_name": "UK| GENERAL"}]
        self.live = {1141: [
            {"name": "### UK GENERAL ###", "stream_id": 1, "epg_channel_id": ""},
            {"name": "UK: BBC 1 HEVC HD", "stream_id": 2, "epg_channel_id": "BBC1.uk", "stream_icon": "http://l/1.png"},
            {"name": "UK: ITV 1 HD", "stream_id": 3, "epg_channel_id": "ITV1.uk"},
        ]}
        self.xmltv_text = XMLTV
        self.fail_xmltv = False

    def vod_categories(self):
        return self.vod_cats

    def series_categories(self):
        return self.series_cats

    def vod_streams(self, category_id):
        if self.fail_lists:
            raise ProviderError("down")
        return self.vod.get(category_id, [])

    def series(self, category_id):
        if self.fail_lists:
            raise ProviderError("down")
        return self.series_lists.get(category_id, [])

    def series_info(self, series_id):
        self.info_calls += 1
        if self.fail_info:
            raise ProviderError("info down")
        return self.infos[series_id]

    def movie_url(self, stream_id, ext):
        return f"M/{stream_id}.{ext}"

    def episode_url(self, episode_id, ext):
        return f"E/{episode_id}.{ext}"

    def live_categories(self):
        return self.live_cats

    def live_streams(self, category_id):
        if self.fail_lists:
            raise ProviderError("down")
        return self.live.get(category_id, [])

    def live_url(self, stream_id):
        return f"L/{stream_id}.ts"

    def xmltv(self):
        if self.fail_xmltv:
            raise ProviderError("xmltv down")
        return self.xmltv_text.encode()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        hosts=("http://h",),
        username="u",
        password="p",
        jellyfin_url="http://j",
        jellyfin_api_key=None,
        interval=1,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        categories_path=tmp_path / "c.yaml",
        layout_path=tmp_path / "layout.yaml",
    )


ALL = Categories(vod=(119,), series=(427,))
MOVIE_STRM = "Movies/Wimbledon (2004)/Wimbledon (2004).strm"
EP1_STRM = "Shows/The Gentlemen (2024)/Season 01/The Gentlemen (2024) S01E01.strm"


def test_first_run_writes_everything(settings):
    client = FakeClient()
    summary = run_once(client, settings, ALL, SeriesCache(settings.state_dir))
    assert (settings.output_dir / MOVIE_STRM).read_text() == "M/2145896.mkv\n"
    assert (settings.output_dir / "Movies/Wimbledon (2004)/movie.nfo").read_text() == (
        "<movie><tmdbid>11823</tmdbid></movie>\n"
    )
    assert (settings.output_dir / EP1_STRM).read_text() == "E/2141519.mkv\n"
    assert (settings.output_dir / "Shows/The Gentlemen (2024)/tvshow.nfo").exists()
    assert (summary.movies, summary.shows, summary.episodes) == (1, 1, 3)
    assert len(summary.diff.added) == 6
    assert (summary.series_fetched, summary.series_cached) == (1, 0)
    assert summary.empty_categories == []
    assert summary.refreshed is None
    assert set(load_manifest(settings.state_dir)) == {
        MOVIE_STRM,
        "Movies/Wimbledon (2004)/movie.nfo",
        "Shows/The Gentlemen (2024)/tvshow.nfo",
        EP1_STRM,
        "Shows/The Gentlemen (2024)/Season 01/The Gentlemen (2024) S01E02.strm",
        "Shows/The Gentlemen (2024)/Season 02/The Gentlemen (2024) S02E01.strm",
    }


def test_second_run_uses_cache_and_removes_dropped_category(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    summary = run_once(client, settings, Categories(vod=(), series=(427,)), cache)
    assert client.info_calls == 1
    assert (summary.series_fetched, summary.series_cached) == (0, 1)
    # F2: Movies is a permanent Jellyfin library root, kept (empty) even
    # though this run selected no vod categories.
    assert (settings.output_dir / "Movies").is_dir()
    assert list((settings.output_dir / "Movies").iterdir()) == []
    assert (settings.output_dir / EP1_STRM).exists()
    # sorted: "W" (0x57) sorts before "m" (0x6d)
    assert summary.diff.removed == [
        MOVIE_STRM,
        "Movies/Wimbledon (2004)/movie.nfo",
    ]


def test_changed_last_modified_refetches(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    client.series_lists[427] = [{**SERIES_ITEM, "last_modified": "999"}]
    run_once(client, settings, ALL, cache)
    assert client.info_calls == 2


def test_list_failure_aborts_without_touching_files(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    client.fail_lists = True
    with pytest.raises(ProviderError):
        run_once(client, settings, ALL, cache)
    assert (settings.output_dir / MOVIE_STRM).exists()
    assert (settings.output_dir / EP1_STRM).exists()


def test_all_empty_aborts(settings):
    client = FakeClient()
    client.vod[119] = []
    client.series_lists[427] = []
    with pytest.raises(RunAborted):
        run_once(client, settings, ALL, SeriesCache(settings.state_dir))
    assert not settings.output_dir.exists()
    assert load_manifest(settings.state_dir) == {}


def test_empty_category_is_reported_not_fatal(settings):
    # A second vod category that's empty while the first has data: the vod
    # kind as a whole isn't empty, so this is a warning, not an abort.
    client = FakeClient()
    client.vod_cats.append({"category_id": "163", "category_name": "EN - NEW RELEASE"})
    client.vod[163] = []
    summary = run_once(
        client, settings, Categories(vod=(119, 163), series=(427,)), SeriesCache(settings.state_dir)
    )
    assert summary.empty_categories == ["vod:163 EN - NEW RELEASE"]
    assert summary.movies == 1


def test_vod_kind_fully_empty_aborts_even_when_series_is_healthy(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    client.vod[119] = []
    with pytest.raises(RunAborted, match="vod"):
        run_once(client, settings, ALL, cache)
    assert (settings.output_dir / MOVIE_STRM).exists()
    assert (settings.output_dir / EP1_STRM).exists()


def test_series_kind_fully_empty_aborts_even_when_vod_is_healthy(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    client.series_lists[427] = []
    with pytest.raises(RunAborted, match="series"):
        run_once(client, settings, ALL, cache)
    assert (settings.output_dir / MOVIE_STRM).exists()
    assert (settings.output_dir / EP1_STRM).exists()


def test_unknown_category_is_reported(settings):
    client = FakeClient()
    summary = run_once(client, settings, Categories(vod=(119, 5), series=()), SeriesCache(settings.state_dir))
    assert summary.empty_categories == ["vod:5 (not offered by provider)"]


def test_series_info_failure_uses_stale_cache(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    client.series_lists[427] = [{**SERIES_ITEM, "last_modified": "999"}]
    client.fail_info = True
    summary = run_once(client, settings, ALL, cache)
    assert summary.shows == 1
    assert (settings.output_dir / EP1_STRM).exists()


def test_series_info_failure_with_no_stale_cache_aborts_series_kind(settings):
    # With no stale cache to fall back to, the one selected series is
    # skipped entirely - which now means the series kind came back
    # completely empty, so the run aborts (I3) rather than silently
    # deleting that series' previously-written files.
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)
    client.series_lists[427] = [{**SERIES_ITEM, "last_modified": "999"}]
    client.fail_info = True

    fresh_cache = SeriesCache(settings.state_dir / "fresh")
    with pytest.raises(RunAborted, match="series"):
        run_once(client, settings, ALL, fresh_cache)
    assert (settings.output_dir / EP1_STRM).exists()


def test_zero_episode_series_info_falls_back_to_stale_cache(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache)

    manifest_path = settings.state_dir / "series" / "50012.json"
    stale_payload = manifest_path.read_text()

    client.series_lists[427] = [{**SERIES_ITEM, "last_modified": "999"}]
    client.infos[50012] = {"episodes": {}}
    summary = run_once(client, settings, ALL, cache)

    assert summary.shows == 1
    assert (settings.output_dir / EP1_STRM).exists()
    assert manifest_path.read_text() == stale_payload


def test_zero_episode_series_info_with_no_stale_cache_aborts_series_kind(settings):
    client = FakeClient()
    client.infos[50012] = {"episodes": {}}
    with pytest.raises(RunAborted, match="series"):
        run_once(client, settings, ALL, SeriesCache(settings.state_dir))


def test_duplicate_items_across_categories_are_written_once(settings):
    client = FakeClient()
    client.vod_cats.append({"category_id": "163", "category_name": "NEW"})
    client.vod[163] = [VOD_ITEM]
    summary = run_once(client, settings, Categories(vod=(119, 163), series=()), SeriesCache(settings.state_dir))
    assert summary.movies == 1


def test_same_tmdb_movie_across_categories_deduped_to_one(settings):
    client = FakeClient()
    client.vod_cats.append({"category_id": "163", "category_name": "NEW"})
    client.vod[163] = [{**VOD_ITEM, "stream_id": 9999999}]  # same tmdb, different stream_id
    summary = run_once(
        client, settings, Categories(vod=(119, 163), series=()), SeriesCache(settings.state_dir)
    )
    assert summary.movies == 1
    assert [p.name for p in (settings.output_dir / "Movies").iterdir()] == ["Wimbledon (2004)"]


def test_malformed_movie_is_skipped(settings):
    client = FakeClient()
    client.vod[119] = [VOD_ITEM, {"name": "broken"}]
    summary = run_once(client, settings, Categories(vod=(119,), series=()), SeriesCache(settings.state_dir))
    assert summary.movies == 1


def test_category_names_skips_malformed_entries():
    assert _category_names(
        [
            {"category_id": "1", "category_name": "A"},
            {"category_id": None, "category_name": "B"},  # TypeError from int(None)
            {"category_name": "C"},  # KeyError, no category_id
            {"category_id": "x", "category_name": "D"},  # ValueError from int("x")
        ]
    ) == {1: "A"}


class BrokenGetItem(dict):
    """A dict whose .get() blows up, to exercise the AttributeError guard."""

    def get(self, *args, **kwargs):
        raise AttributeError("simulated")


def test_movie_attribute_error_is_skipped(settings):
    client = FakeClient()
    client.vod[119] = [VOD_ITEM, BrokenGetItem({**VOD_ITEM, "stream_id": 999})]
    summary = run_once(client, settings, Categories(vod=(119,), series=()), SeriesCache(settings.state_dir))
    assert summary.movies == 1


class BrokenTmdbItem(dict):
    """A dict whose .get("tmdb") blows up; other keys behave normally."""

    def get(self, key, default=None):
        if key == "tmdb":
            raise AttributeError("simulated")
        return super().get(key, default)


def test_show_attribute_error_is_skipped(settings):
    client = FakeClient()
    broken = BrokenTmdbItem({**SERIES_ITEM, "series_id": 999})
    client.series_lists[427] = [SERIES_ITEM, broken]
    client.infos[999] = SERIES_INFO
    summary = run_once(client, settings, ALL, SeriesCache(settings.state_dir))
    assert summary.shows == 1


def test_refresh_called_when_key_set(settings):
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    calls = []

    def fake_refresh(url, key):
        calls.append((url, key))
        return True

    summary = run_once(FakeClient(), settings, ALL, SeriesCache(settings.state_dir), refresh=fake_refresh)
    assert calls == [("http://j", "k")]
    assert summary.refreshed is True


def test_refresh_skipped_when_nothing_changed(settings):
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    calls = []

    def fake_refresh(url, key):
        calls.append((url, key))
        return True

    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    first = run_once(client, settings, ALL, cache, refresh=fake_refresh)
    second = run_once(client, settings, ALL, cache, refresh=fake_refresh)
    assert first.refreshed is True
    assert second.refreshed is None
    assert len(calls) == 1


def test_emptied_output_volume_is_restored_and_triggers_refresh(settings):
    # F1: the planned cutover keeps the manifest but starts from an empty
    # output volume. plan_diff sees no change (the manifest already lists
    # every file), so apply_diff's "tracked but missing on disk" branch does
    # all the writing; that must surface as diff.restored and still trigger
    # a jellyfin refresh, even though diff.added is empty.
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    calls = []

    def fake_refresh(url, key):
        calls.append((url, key))
        return True

    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, ALL, cache, refresh=fake_refresh)
    assert len(calls) == 1

    shutil.rmtree(settings.output_dir)

    summary = run_once(client, settings, ALL, cache, refresh=fake_refresh)
    assert (settings.output_dir / MOVIE_STRM).exists()
    assert (settings.output_dir / EP1_STRM).exists()
    assert summary.diff.restored != []
    assert summary.diff.added == []
    assert len(calls) == 2


def test_live_writes_m3u_and_filtered_guide(settings):
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    guide_calls = []
    summary = run_once(
        FakeClient(), settings, LIVE, SeriesCache(settings.state_dir),
        refresh=lambda u, k: True, refresh_guide=lambda u, k: guide_calls.append(u) or True,
    )
    m3u = (settings.output_dir / "live.m3u").read_text()
    assert m3u.splitlines()[0] == "#EXTM3U"
    assert 'tvg-id="BBC1.uk" tvg-chno="1" tvg-name="BBC 1"' in m3u
    assert "L/2.ts" in m3u and "L/3.ts" in m3u and "L/1.ts" not in m3u
    guide = (settings.output_dir / "guide.xml").read_text()
    assert "BBC1.uk" in guide and "ITV1.uk" in guide and "Other.hu" not in guide
    assert (summary.channels, summary.programmes) == (2, 1)
    assert summary.guide_refreshed is True and guide_calls == ["http://j"]
    # F2: Movies/Shows are permanent Jellyfin library roots, kept (empty)
    # even on a live-only run that placed nothing into them.
    assert (settings.output_dir / "Movies").is_dir()
    assert list((settings.output_dir / "Movies").iterdir()) == []


def test_live_m3u_group_title_uses_cleaned_category_name(settings):
    # I2: the category name from the provider may carry decoration
    # characters (e.g. superscript codec tags); group-title must use the
    # same cleaned name categories_template.clean_name would render.
    client = FakeClient()
    client.live_cats = [{"category_id": "1141", "category_name": "UK| GENERAL ʰᵉᵛᶜ"}]
    run_once(client, settings, LIVE, SeriesCache(settings.state_dir))
    m3u = (settings.output_dir / "live.m3u").read_text()
    assert 'group-title="UK| GENERAL"' in m3u
    assert "ʰ" not in m3u


def test_live_guide_failure_keeps_previous_guide(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, LIVE, cache)
    before = (settings.output_dir / "guide.xml").read_text()
    client.fail_xmltv = True
    summary = run_once(client, settings, LIVE, cache)
    assert (settings.output_dir / "guide.xml").read_text() == before
    assert (settings.output_dir / "live.m3u").exists()
    # The carried-forward guide's programme count is reported from the kept
    # text itself, not hardcoded to 0 (the previous guide.xml does have
    # programmes in it - just not freshly fetched ones).
    assert summary.programmes == before.count("<programme ")
    assert summary.programmes == 1


def test_live_guide_failure_on_first_run_omits_guide(settings):
    client = FakeClient()
    client.fail_xmltv = True
    run_once(client, settings, LIVE, SeriesCache(settings.state_dir))
    assert (settings.output_dir / "live.m3u").exists()
    assert not (settings.output_dir / "guide.xml").exists()


def test_live_all_empty_aborts(settings):
    client = FakeClient()
    client.live[1141] = []
    with pytest.raises(RunAborted, match="live"):
        run_once(client, settings, LIVE, SeriesCache(settings.state_dir))
    assert not settings.output_dir.exists()


def test_live_only_headers_counts_as_empty(settings):
    client = FakeClient()
    client.live[1141] = [{"name": "### ONLY HEADER ###", "stream_id": 1}]
    with pytest.raises(RunAborted, match="live"):
        run_once(client, settings, LIVE, SeriesCache(settings.state_dir))


def test_guide_refresh_only_when_live_files_change(settings):
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    calls = []
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    kwargs = {"refresh": lambda u, k: True, "refresh_guide": lambda u, k: calls.append(1) or True}
    run_once(client, settings, LIVE, cache, **kwargs)
    second = run_once(client, settings, LIVE, cache, **kwargs)
    assert calls == [1]
    assert second.guide_refreshed is None


def test_guide_only_change_does_not_trigger_library_refresh(settings):
    # guide.xml changes every run (provider regenerates it); that alone must
    # not request a full Jellyfin library scan (I1), only the guide refresh.
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    library_calls = []
    guide_calls = []
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    both = Categories(vod=(119,), series=(), live=(1141,))
    kwargs = {
        "refresh": lambda u, k: library_calls.append(1) or True,
        "refresh_guide": lambda u, k: guide_calls.append(1) or True,
    }
    first = run_once(client, settings, both, cache, **kwargs)
    assert first.refreshed is True
    assert first.guide_refreshed is True
    assert len(library_calls) == 1
    assert len(guide_calls) == 1

    # Only the guide text changes (provider adds a programme); nothing else.
    client.xmltv_text = client.xmltv_text.replace(
        "</tv>", '<programme start="20260910220000 +0100" '
        'stop="20260910230000 +0100" channel="BBC1.uk"><title>Late</title></programme>\n</tv>'
    )
    second = run_once(client, settings, both, cache, **kwargs)
    assert second.refreshed is None
    assert second.guide_refreshed is True
    assert len(library_calls) == 1
    assert len(guide_calls) == 2


def test_removing_live_selection_removes_live_files(settings):
    client = FakeClient()
    cache = SeriesCache(settings.state_dir)
    run_once(client, settings, Categories(vod=(119,), series=(), live=(1141,)), cache)
    assert (settings.output_dir / "live.m3u").exists()
    run_once(client, settings, Categories(vod=(119,), series=(), live=()), cache)
    assert not (settings.output_dir / "live.m3u").exists()
    assert not (settings.output_dir / "guide.xml").exists()
    assert (settings.output_dir / MOVIE_STRM).exists()


def test_layout_routes_and_skips_and_collides(settings, tmp_path):
    real = tmp_path / "real/Shows/The Gentlemen (2024)"
    real.mkdir(parents=True)
    (tmp_path / "layout.yaml").write_text(
        f"""
rules:
  - match: {{ title: "^Wimbledon" }}
    into: "Sport/{{kind}}"
collisions:
  shows: ["{tmp_path / 'real/Shows'}"]
"""
    )
    client = FakeClient()
    layout = load_layout(tmp_path / "layout.yaml")
    summary = run_once(client, settings, ALL, SeriesCache(settings.state_dir), layout=layout)
    assert (settings.output_dir / "Sport/Movies/Wimbledon (2004)/Wimbledon (2004).strm").exists()
    assert not (settings.output_dir / EP1_STRM).exists()
    assert (summary.movies, summary.shows) == (1, 1)
    assert (summary.skipped, summary.collided, summary.rules) == (0, 1, 1)
    assert len(summary.diff.added) == 2


def test_rule_change_moves_files_without_rewriting(settings, tmp_path):
    client = FakeClient()
    run_once(client, settings, ALL, SeriesCache(settings.state_dir))
    before = (settings.output_dir / MOVIE_STRM).stat().st_mtime_ns
    (tmp_path / "layout.yaml").write_text('rules:\n  - match: { kind: movie }\n    into: Films\n')
    layout = load_layout(tmp_path / "layout.yaml")
    summary = run_once(client, settings, ALL, SeriesCache(settings.state_dir), layout=layout)
    moved = settings.output_dir / "Films/Wimbledon (2004)/Wimbledon (2004).strm"
    assert moved.stat().st_mtime_ns == before
    # F2: Movies is still a configured Jellyfin library root, so it is kept
    # (empty) rather than pruned even though nothing routes there anymore.
    assert (settings.output_dir / "Movies").is_dir()
    assert list((settings.output_dir / "Movies").iterdir()) == []
    assert summary.diff.moved == [
        (MOVIE_STRM, "Films/Wimbledon (2004)/Wimbledon (2004).strm"),
        ("Movies/Wimbledon (2004)/movie.nfo", "Films/Wimbledon (2004)/movie.nfo"),
    ]
    assert summary.diff.added == [] and summary.diff.removed == []
    manifest = load_manifest(settings.state_dir)
    assert "Films/Wimbledon (2004)/movie.nfo" in manifest and MOVIE_STRM not in manifest


def test_move_counts_as_media_change_for_refresh(settings, tmp_path):
    client = FakeClient()
    settings = dataclasses.replace(settings, jellyfin_api_key="k")
    calls = []
    run_once(client, settings, ALL, SeriesCache(settings.state_dir), refresh=lambda *a: calls.append(a) or True)
    (tmp_path / "layout.yaml").write_text('rules:\n  - match: { kind: movie }\n    into: Films\n')
    run_once(client, settings, ALL, SeriesCache(settings.state_dir),
             refresh=lambda *a: calls.append(a) or True, layout=load_layout(tmp_path / "layout.yaml"))
    assert len(calls) == 2


from xtream_sync.sync import check_layout


def test_check_layout_report(tmp_path):
    (tmp_path / "layout.yaml").write_text(
        'rules:\n  - match: { kind: movie }\n    into: Films\n  - match: { title: Gentlemen }\n    skip: true\n'
    )
    text = check_layout(FakeClient(), ALL, load_layout(tmp_path / "layout.yaml"))
    assert text.splitlines() == [
        "vod 119 EN - 2020 & OLD",
        "  Wimbledon (2004)  ->  Films/Wimbledon (2004)",
        "series 427 NETFLIX SERIES",
        "  The Gentlemen (2024)  ->  skip (rule 1)",
    ]


def test_check_layout_marks_collisions(tmp_path):
    real = tmp_path / "real/Movies/Wimbledon (2004)"
    real.mkdir(parents=True)
    (tmp_path / "layout.yaml").write_text(f'collisions:\n  movies: ["{tmp_path / "real/Movies"}"]\n')
    text = check_layout(FakeClient(), Categories(vod=(119,), series=()), load_layout(tmp_path / "layout.yaml"))
    assert "  Wimbledon (2004)  ->  collision (already in a real library)" in text
