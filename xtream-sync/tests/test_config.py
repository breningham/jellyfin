from pathlib import Path

import pytest

from xtream_sync.config import Categories, ConfigError, load_categories, load_settings

BASE_ENV = {
    "XTREAM_HOST": "http://host.example/",
    "XTREAM_USERNAME": "u",
    "XTREAM_PASSWORD": "p",
}


def test_load_settings_defaults():
    s = load_settings(BASE_ENV)
    assert s.hosts == ("http://host.example",)
    assert (s.username, s.password) == ("u", "p")
    assert s.jellyfin_url == "http://jellyfin:8096"
    assert s.jellyfin_api_key is None
    assert s.interval == 21600
    assert s.output_dir == Path("/output")
    assert s.state_dir == Path("/state")
    assert s.categories_path == Path("/config/categories.yaml")


def test_layout_path_default_and_override(monkeypatch):
    env = {"XTREAM_HOST": "http://h", "XTREAM_USERNAME": "u", "XTREAM_PASSWORD": "p"}
    assert load_settings(env).layout_path == Path("/config/layout.yaml")
    assert load_settings({**env, "XTREAM_LAYOUT": "/x/l.yaml"}).layout_path == Path("/x/l.yaml")


def test_load_settings_overrides():
    env = {
        **BASE_ENV,
        "JELLYFIN_URL": "http://j:1/",
        "JELLYFIN_API_KEY": "key",
        "XTREAM_SYNC_INTERVAL": "60",
        "XTREAM_OUTPUT_DIR": "/tmp/o",
        "XTREAM_STATE_DIR": "/tmp/s",
        "XTREAM_CATEGORIES": "/tmp/c.yaml",
    }
    s = load_settings(env)
    assert s.jellyfin_url == "http://j:1"
    assert s.jellyfin_api_key == "key"
    assert s.interval == 60
    assert (s.output_dir, s.state_dir, s.categories_path) == (
        Path("/tmp/o"),
        Path("/tmp/s"),
        Path("/tmp/c.yaml"),
    )


def test_empty_api_key_is_none():
    assert load_settings({**BASE_ENV, "JELLYFIN_API_KEY": ""}).jellyfin_api_key is None


@pytest.mark.parametrize("missing", ["XTREAM_HOST", "XTREAM_USERNAME", "XTREAM_PASSWORD"])
def test_missing_required_var(missing):
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_bad_interval(value):
    with pytest.raises(ConfigError, match="XTREAM_SYNC_INTERVAL"):
        load_settings({**BASE_ENV, "XTREAM_SYNC_INTERVAL": value})


def test_hosts_parsed_from_comma_list():
    env = {**BASE_ENV, "XTREAM_HOST": " http://a.example/ , http://b.example:8080 ,, "}
    assert load_settings(env).hosts == ("http://a.example", "http://b.example:8080")


def test_hosts_all_blank_is_missing():
    with pytest.raises(ConfigError, match="XTREAM_HOST"):
        load_settings({**BASE_ENV, "XTREAM_HOST": " , "})


def test_load_categories(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("vod:\n  - 163  # EN - NEW RELEASE\n  - 287\nseries:\n  - 427\n")
    assert load_categories(path) == Categories(vod=(163, 287), series=(427,))


def test_load_categories_one_list_may_be_absent(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("vod: [1]\n")
    assert load_categories(path) == Categories(vod=(1,), series=())


def test_load_categories_live_list(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("live:\n  - 1141  # UK| GENERAL\n  - 1145\n")
    assert load_categories(path) == Categories(vod=(), series=(), live=(1141, 1145))


def test_categories_live_defaults_empty():
    assert Categories(vod=(1,), series=()).live == ()


@pytest.mark.parametrize(
    "text",
    ["", "vod: []\nseries: []\nlive: []\n", "- 1\n- 2\n", "vod: 5\n", "vod: ['a']\n"],
)
def test_load_categories_rejects_bad_files(tmp_path, text):
    path = tmp_path / "c.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_categories(path)


def test_load_categories_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_categories(tmp_path / "nope.yaml")


def test_committed_categories_example_loads():
    path = Path(__file__).resolve().parents[2] / "config/xtream/categories.example.yaml"
    categories = load_categories(path)
    assert categories.vod or categories.series
    assert all(isinstance(i, int) for i in categories.vod + categories.series)
