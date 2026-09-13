from xtream_sync.series_cache import SeriesCache


def test_get_miss_and_put_hit(tmp_path):
    cache = SeriesCache(tmp_path)
    assert cache.get(1, "100") is None
    cache.put(1, "100", {"episodes": {}})
    assert cache.get(1, "100") == {"episodes": {}}


def test_stale_entry_is_a_miss_unless_unconditional(tmp_path):
    cache = SeriesCache(tmp_path)
    cache.put(1, "100", {"v": 1})
    assert cache.get(1, "200") is None
    assert cache.get(1, None) == {"v": 1}


def test_corrupt_entry_is_a_miss(tmp_path):
    cache = SeriesCache(tmp_path)
    cache.put(1, "100", {"v": 1})
    (tmp_path / "series" / "1.json").write_text("{not json")
    assert cache.get(1, "100") is None


def test_last_modified_compared_as_string(tmp_path):
    cache = SeriesCache(tmp_path)
    cache.put(2, 1788879650, {"v": 2})
    assert cache.get(2, "1788879650") == {"v": 2}
