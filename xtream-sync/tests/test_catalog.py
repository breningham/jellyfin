import pytest

from tests.fixtures import SERIES_INFO, SERIES_ITEM, VOD_ITEM
from xtream_sync.catalog import (
    Episode,
    Movie,
    Show,
    build_files,
    dedupe_by_tmdb,
    parse_movie,
    parse_show,
)
from xtream_sync.catalog import Built, candidate
from xtream_sync.layout import Candidate, Destination


def movie_url(stream_id, ext):
    return f"M/{stream_id}.{ext}"


def episode_url(episode_id, ext):
    return f"E/{episode_id}.{ext}"


def test_parse_movie():
    assert parse_movie(VOD_ITEM, "EN - 2020 & OLD") == Movie(
        2145896, "Wimbledon", 2004, "mkv", "11823",
        category="EN - 2020 & OLD", category_id=119, tags=("EN",),
    )


def test_parse_movie_defaults_and_bad_tmdb():
    item = {**VOD_ITEM, "container_extension": "", "tmdb": ""}
    assert parse_movie(item).ext == "mp4"
    assert parse_movie(item).tmdb is None
    assert parse_movie({**VOD_ITEM, "tmdb": "n/a"}).tmdb is None
    assert parse_movie({**VOD_ITEM, "tmdb": 11823}).tmdb == "11823"


def test_parse_movie_missing_id_raises():
    with pytest.raises(KeyError):
        parse_movie({"name": "x"})


def test_parse_show_flattens_and_sorts_episodes():
    show = parse_show(SERIES_ITEM, SERIES_INFO)
    assert show.series_id == 50012
    assert (show.title, show.year, show.tmdb) == ("The Gentlemen", 2024, "236235")
    assert show.episodes == (
        Episode(2141519, 1, 1, "mkv"),
        Episode(2141520, 1, 2, "mkv"),
        Episode(2141600, 2, 1, "mp4"),
    )


def test_parse_show_accepts_list_style_episodes():
    info = {"episodes": [[{"id": 5, "episode_num": 3, "season": 4, "container_extension": "ts"}]]}
    assert parse_show(SERIES_ITEM, info).episodes == (Episode(5, 4, 3, "ts"),)


def test_parse_show_with_no_episodes():
    assert parse_show(SERIES_ITEM, {}).episodes == ()


def test_parse_show_accepts_dict_of_dicts_episodes():
    # Some panels return a season group as a dict keyed by episode number
    # instead of a list.
    info = {
        "episodes": {
            "1": {
                "1": {"id": 5, "episode_num": 1, "season": 1, "container_extension": "mkv"},
                "2": {"id": 6, "episode_num": 2, "season": 1, "container_extension": "mkv"},
            }
        }
    }
    show = parse_show(SERIES_ITEM, info)
    assert show.episodes == (
        Episode(5, 1, 1, "mkv"),
        Episode(6, 1, 2, "mkv"),
    )


def test_parse_show_skips_malformed_episode_but_keeps_the_rest(caplog):
    info = {
        "episodes": {
            "1": [
                {"id": 5, "episode_num": 1, "season": 1, "container_extension": "mkv"},
                {"id": 6, "episode_num": None, "season": 1, "container_extension": "mkv"},
            ]
        }
    }
    with caplog.at_level("WARNING"):
        show = parse_show(SERIES_ITEM, info)
    assert show.episodes == (Episode(5, 1, 1, "mkv"),)
    assert "50012" in caplog.text or "malformed" in caplog.text.lower()


def test_parse_show_skips_episode_missing_id(caplog):
    info = {
        "episodes": {
            "1": [
                {"id": 5, "episode_num": 1, "season": 1, "container_extension": "mkv"},
                {"episode_num": 2, "season": 1, "container_extension": "mkv"},
            ]
        }
    }
    with caplog.at_level("WARNING"):
        show = parse_show(SERIES_ITEM, info)
    assert show.episodes == (Episode(5, 1, 1, "mkv"),)


def test_build_files():
    movies = [Movie(1, "Dune", 2021, "mkv", "438631"), Movie(2, "No Tmdb", None, "mp4", None)]
    shows = [
        Show(9, "The Gentlemen", 2024, "236235", (Episode(11, 1, 1, "mkv"), Episode(12, 1, 2, "mkv"))),
        Show(10, "Empty", None, "1", ()),
    ]
    files = build_files(movies, shows, movie_url, episode_url).files
    assert files == {
        "Movies/Dune (2021)/Dune (2021).strm": "M/1.mkv\n",
        "Movies/Dune (2021)/movie.nfo": "<movie><tmdbid>438631</tmdbid></movie>\n",
        "Movies/No Tmdb/No Tmdb.strm": "M/2.mp4\n",
        "Shows/The Gentlemen (2024)/tvshow.nfo": "<tvshow><tmdbid>236235</tmdbid></tvshow>\n",
        "Shows/The Gentlemen (2024)/Season 01/The Gentlemen (2024) S01E01.strm": "E/11.mkv\n",
        "Shows/The Gentlemen (2024)/Season 01/The Gentlemen (2024) S01E02.strm": "E/12.mkv\n",
    }


def test_dedupe_by_tmdb_keeps_newest_id():
    movies = [Movie(20, "A", None, "mkv", "100"), Movie(10, "B", None, "mkv", "100")]
    assert dedupe_by_tmdb(movies, lambda m: m.stream_id) == [Movie(20, "A", None, "mkv", "100")]


def test_dedupe_by_tmdb_prefers_4k_over_newer_hd():
    movies = [
        Movie(90, "HD newer", None, "mkv", "100", is_4k=False),
        Movie(10, "4K older", None, "mkv", "100", is_4k=True),
        Movie(50, "4K mid", None, "mkv", "100", is_4k=True),
    ]
    assert dedupe_by_tmdb(movies, lambda m: m.stream_id) == [Movie(50, "4K mid", None, "mkv", "100", is_4k=True)]


def test_is_4k_detected_from_item_name_or_category():
    assert parse_movie({**VOD_ITEM, "name": "4K-NF - Dune (2021)"}).is_4k is True
    assert parse_movie(VOD_ITEM, "NETFLIX MOVIES \u2074\u1d37 \u00b3\u2078\u2074\u2070\u1d3e").is_4k is True
    assert parse_movie(VOD_ITEM, "NETFLIX MOVIES 4K").is_4k is True
    assert parse_movie(VOD_ITEM, "EN - DRAMA").is_4k is False
    assert parse_show(SERIES_ITEM, SERIES_INFO, "NETFLIX SERIES").is_4k is True  # name starts "4K-NF"
    assert parse_show({**SERIES_ITEM, "name": "The Gentlemen (2024)"}, SERIES_INFO, "NETFLIX SERIES").is_4k is False


def test_dedupe_by_tmdb_three_sharing_newest_wins_ordered():
    movies = [
        Movie(30, "A", None, "mkv", "100"),
        Movie(10, "B", None, "mkv", "100"),
        Movie(20, "C", None, "mkv", "100"),
        Movie(5, "D", None, "mkv", "200"),
    ]
    assert dedupe_by_tmdb(movies, lambda m: m.stream_id) == [
        Movie(5, "D", None, "mkv", "200"),
        Movie(30, "A", None, "mkv", "100"),
    ]


def test_dedupe_by_tmdb_keeps_all_untagged():
    movies = [Movie(2, "A", None, "mkv", None), Movie(1, "B", None, "mkv", None)]
    assert dedupe_by_tmdb(movies, lambda m: m.stream_id) == [
        Movie(1, "B", None, "mkv", None),
        Movie(2, "A", None, "mkv", None),
    ]


def test_dedupe_by_tmdb_shows_keeps_newest_with_episodes_intact():
    shows = [
        Show(10, "A", None, "50", (Episode(1, 1, 1, "mkv"),)),
        Show(20, "B", None, "50", (Episode(2, 1, 1, "mkv"), Episode(3, 1, 2, "mkv"))),
    ]
    result = dedupe_by_tmdb(shows, lambda s: s.series_id)
    assert result == [shows[1]]
    assert result[0].episodes == (Episode(2, 1, 1, "mkv"), Episode(3, 1, 2, "mkv"))


def test_dedupe_then_build_files_collapses_to_one_folder():
    movies = [Movie(20, "Dune", 2021, "mkv", "438631"), Movie(10, "Dune", 2021, "mkv", "438631")]
    deduped = dedupe_by_tmdb(movies, lambda m: m.stream_id)
    files = build_files(deduped, [], movie_url, episode_url).files
    assert set(files) == {
        "Movies/Dune (2021)/Dune (2021).strm",
        "Movies/Dune (2021)/movie.nfo",
    }


def test_dedupe_keeps_different_tmdb_and_build_files_still_suffixes():
    movies = [Movie(20, "Dune", 2021, "mkv", "1"), Movie(10, "Dune", 2021, "mkv", "2")]
    deduped = dedupe_by_tmdb(movies, lambda m: m.stream_id)
    assert len(deduped) == 2
    files = build_files(deduped, [], movie_url, episode_url).files
    assert set(files) == {
        "Movies/Dune (2021)/Dune (2021).strm",
        "Movies/Dune (2021)/movie.nfo",
        "Movies/Dune (2021) [xtream-20]/Dune (2021) [xtream-20].strm",
        "Movies/Dune (2021) [xtream-20]/movie.nfo",
    }


def test_build_files_collisions_get_suffix():
    movies = [Movie(20, "Dune", 2021, "mkv", None), Movie(10, "Dune", 2021, "mkv", None)]
    files = build_files(movies, [], movie_url, episode_url).files
    assert set(files) == {
        "Movies/Dune (2021)/Dune (2021).strm",
        "Movies/Dune (2021) [xtream-20]/Dune (2021) [xtream-20].strm",
    }


def test_parse_show_carries_category_and_tags():
    show = parse_show(SERIES_ITEM, SERIES_INFO, "NETFLIX SERIES")
    assert (show.category, show.category_id, show.tags) == ("NETFLIX SERIES", 427, ("4K-NF",))


def test_candidate_from_movie_and_show():
    movie = Movie(1, "Dune", 2021, "mkv", "438631", is_4k=True, category="TOP", category_id=3, tags=("EN",))
    assert candidate(movie) == Candidate("movie", "Dune", 2021, "438631", "TOP", 3, ("EN",), True)
    show = Show(9, "Gent", 2024, "236235", (), category="NF", category_id=4)
    assert candidate(show) == Candidate("series", "Gent", 2024, "236235", "NF", 4, (), False)


def test_build_files_uses_resolver_and_groups_per_library():
    def resolve(c):
        if c.title == "Skip me":
            return None
        library = "Kids/" + ("Movies" if c.kind == "movie" else "Shows") if c.category == "KIDS" else None
        return Destination(library or ("Movies" if c.kind == "movie" else "Shows"), f"{c.title} [{c.tmdb}]")

    movies = [
        Movie(1, "Dune", 2021, "mkv", "1", category="KIDS"),
        Movie(2, "Dune", 2021, "mkv", "2", category="TOP"),
        Movie(3, "Dune", 2021, "mkv", "1", category="TOP"),
        Movie(4, "Skip me", None, "mkv", None),
    ]
    shows = [Show(9, "Gent", 2024, "5", (Episode(11, 1, 1, "mkv"),), category="KIDS")]
    built = build_files(movies, shows, movie_url, episode_url, resolve=resolve)
    assert set(built.files) == {
        "Kids/Movies/Dune [1]/Dune [1].strm",
        "Kids/Movies/Dune [1]/movie.nfo",
        "Movies/Dune [2]/Dune [2].strm",
        "Movies/Dune [2]/movie.nfo",
        "Movies/Dune [1]/Dune [1].strm",
        "Movies/Dune [1]/movie.nfo",
        "Kids/Shows/Gent [5]/tvshow.nfo",
        "Kids/Shows/Gent [5]/Season 01/Gent [5] S01E01.strm",
    }
    assert built.libraries == {"Kids/Movies", "Movies", "Kids/Shows"}
    assert (built.skipped, built.collided) == (1, 0)


def test_build_files_suffixes_duplicates_only_within_a_library():
    def resolve(c):
        return Destination("A" if c.category == "a" else "B", "Same")

    movies = [Movie(1, "x", None, "mkv", None, category="a"), Movie(2, "y", None, "mkv", None, category="b"),
              Movie(3, "z", None, "mkv", None, category="a")]
    built = build_files(movies, [], movie_url, episode_url, resolve=resolve)
    assert set(built.files) == {"A/Same/Same.strm", "B/Same/Same.strm", "A/Same [xtream-3]/Same [xtream-3].strm"}


def test_build_files_skips_collisions():
    seen = []

    def collides(kind, folder, tmdb):
        seen.append((kind, folder, tmdb))
        return folder == "Dune (2021)"

    movies = [Movie(1, "Dune", 2021, "mkv", "1"), Movie(2, "Other", None, "mkv", None)]
    built = build_files(movies, [], movie_url, episode_url, collides=collides)
    assert set(built.files) == {"Movies/Other/Other.strm"}
    assert built.collided == 1
    assert ("movie", "Dune (2021)", "1") in seen


def test_build_files_default_is_unchanged_layout():
    built = build_files([Movie(1, "Dune", 2021, "mkv", None)], [], movie_url, episode_url)
    assert built == Built(files={"Movies/Dune (2021)/Dune (2021).strm": "M/1.mkv\n"}, libraries={"Movies"})
