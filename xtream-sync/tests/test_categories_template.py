from datetime import date

import pytest
import yaml

from xtream_sync.categories_template import clean_name, render
from xtream_sync.config import Categories, ConfigError, load_categories


class FakeClient:
    def vod_categories(self):
        return [
            {"category_id": "219", "category_name": "NETFLIX MOVIES ⁴ᴷ ³⁸⁴⁰ᴾ ᴰᵒˡᵇʸ ⱽⁱˢⁱᵒⁿ"},
            {"category_id": "137", "category_name": "NETFLIX MOVIES"},
        ]

    def series_categories(self):
        return [{"category_id": "427", "category_name": "NETFLIX SERIES ⁴ᴷ"}]

    def live_categories(self):
        return [{"category_id": "1141", "category_name": "UK| GENERAL"}]


def test_clean_name_strips_decorations_and_tags():
    assert clean_name("NETFLIX MOVIES ⁴ᴷ ³⁸⁴⁰ᴾ ᴰᵒˡᵇʸ ⱽⁱˢⁱᵒⁿ") == "NETFLIX MOVIES  [4K Dolby]"
    assert clean_name("EN - DRAMA") == "EN - DRAMA"
    assert clean_name("TOP MOVIES ʸ") == "TOP MOVIES"


def test_render_lists_all_and_enables_current_selection():
    text = render(FakeClient(), Categories(vod=(219,), series=()), today=date(2026, 9, 10))
    assert "2026-09-10" in text
    assert "  - 219   # NETFLIX MOVIES  [4K Dolby]" in text
    assert "  #- 137   # NETFLIX MOVIES" in text
    assert "  #- 427   # NETFLIX SERIES  [4K]" in text
    parsed = yaml.safe_load(text)
    assert parsed == {"vod": [219], "series": None, "live": None}


def test_render_includes_live_section():
    text = render(FakeClient(), Categories(vod=(), series=(), live=(1141,)), today=date(2026, 9, 10))
    assert "live:" in text
    assert "  - 1141  # UK| GENERAL" in text
    assert yaml.safe_load(text) == {"vod": None, "series": None, "live": [1141]}


def test_rendered_file_round_trips_through_load_categories(tmp_path):
    """What `make xtream-categories` writes must be what the sidecar reads."""
    current = Categories(vod=(219,), series=(427,), live=(1141,))
    path = tmp_path / "categories.yaml"
    path.write_text(render(FakeClient(), current, today=date(2026, 9, 10)), encoding="utf-8")
    assert load_categories(path) == current



def test_rendered_file_with_nothing_enabled_is_rejected_on_load(tmp_path):
    path = tmp_path / "categories.yaml"
    path.write_text(render(FakeClient(), Categories(vod=(), series=()), today=date(2026, 9, 10)))
    with pytest.raises(ConfigError, match="no categories selected"):
        load_categories(path)
