import logging

from xtream_sync.collisions import EMPTY_INDEX, CollisionIndex


def test_empty_index_never_collides():
    assert EMPTY_INDEX.collides("movie", "Heat (1995)", "949") is False


def test_name_match_ignores_case_and_unsafe_characters(tmp_path):
    (tmp_path / "Movies/Face-Off (1997)").mkdir(parents=True)
    index = CollisionIndex.build([tmp_path / "Movies"], [])
    assert index.collides("movie", "face/off (1997)", None) is True
    assert index.collides("movie", "Face-Off (1997)", None) is True
    assert index.collides("movie", "Face-Off (1998)", None) is False
    assert index.collides("series", "Face-Off (1997)", None) is False


def test_tmdb_match_from_nfo(tmp_path):
    movie = tmp_path / "Movies/Heat"
    movie.mkdir(parents=True)
    (movie / "movie.nfo").write_text("<movie><title>Heat</title><tmdbid>949</tmdbid></movie>")
    show = tmp_path / "Shows/Gentlemen"
    show.mkdir(parents=True)
    (show / "tvshow.nfo").write_text('<tvshow><uniqueid type="tmdb" default="true">236235</uniqueid></tvshow>')
    index = CollisionIndex.build([tmp_path / "Movies"], [tmp_path / "Shows"])
    assert index.collides("movie", "Heat (1995)", "949") is True
    assert index.collides("movie", "Heat (1995)", "950") is False
    assert index.collides("series", "The Gentlemen (2024)", "236235") is True


def test_files_and_huge_nfo_are_ignored(tmp_path):
    (tmp_path / "Movies").mkdir()
    (tmp_path / "Movies/loose.mkv").write_text("x")
    big = tmp_path / "Movies/Big"
    big.mkdir()
    (big / "movie.nfo").write_text("<tmdbid>1</tmdbid>" + "x" * 70_000)
    index = CollisionIndex.build([tmp_path / "Movies"], [])
    assert index.collides("movie", "loose.mkv", None) is False
    assert index.collides("movie", "Big", None) is True
    assert index.collides("movie", "Other", "1") is False


def test_missing_directory_warns_and_is_empty(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        index = CollisionIndex.build([tmp_path / "nope"], [])
    assert index.collides("movie", "Anything", None) is False
    assert "nope" in caplog.text
