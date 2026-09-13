import re

import pytest

from xtream_sync.layout import Candidate, TemplateError, render


def cand(**overrides):
    base = dict(kind="movie", title="Heat", year=1995, tmdb="949", category="TOP MOVIES [4K]", category_id=7)
    return Candidate(**{**base, **overrides})


def test_variables():
    assert cand().variables() == {
        "title": "Heat",
        "year": "1995",
        "tmdb": "949",
        "kind": "Movies",
        "category": "TOP MOVIES [4K]",
        "letter": "H",
        "decade": "1990s",
    }


def test_variables_without_year_and_tmdb_and_odd_title():
    v = cand(kind="series", year=None, tmdb=None, title="9 to 5", category="A: B").variables()
    assert (v["year"], v["tmdb"], v["decade"], v["letter"], v["kind"]) == ("", "", "", "#", "Shows")
    assert v["category"] == "A- B"


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{title} ({year})", "Heat (1995)"),
        ("{kind}", "Movies"),
        ("Kids/{kind}", "Kids/Movies"),
        ("{title} ({year}) [4K]", "Heat (1995) [4K]"),
        ("{letter}/{title}", "H/Heat"),
        ("{decade}/{title}", "1990s/Heat"),
    ],
)
def test_render(template, expected):
    assert render(template, cand().variables()) == expected


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{title} ({year})", "Heat"),
        ("{title} ({year}) [4K]", "Heat [4K]"),
        ("{title} [{tmdb}]", "Heat"),
        ("{decade}/{title}", "Heat"),
        ("{title}   ( {year} )", "Heat"),
    ],
)
def test_render_tidies_empty_brackets_and_components(template, expected):
    assert render(template, cand(year=None, tmdb=None).variables()) == expected


def test_title_variable_is_path_safe_and_render_makes_components_safe():
    assert cand(title="Face/Off: Part 2?").variables()["title"] == "Face-Off- Part 2-"
    assert render("{title}", cand(title="Face/Off: Part 2?").variables()) == "Face-Off- Part 2-"
    assert render("{title}/x. ", cand().variables()) == "Heat/x"


def test_render_unknown_variable():
    with pytest.raises(TemplateError, match="unknown template variable {nope}"):
        render("{title} {nope}", cand().variables())


from pathlib import Path

from xtream_sync.config import ConfigError
from xtream_sync.layout import DEFAULT_LAYOUT, Destination, Layout, load_layout


def layout(text: str, tmp_path) -> Layout:
    path = tmp_path / "layout.yaml"
    path.write_text(text, encoding="utf-8")
    return load_layout(path)


def test_missing_or_empty_file_is_default(tmp_path):
    assert load_layout(tmp_path / "nope.yaml") == DEFAULT_LAYOUT
    assert layout("", tmp_path) == DEFAULT_LAYOUT
    assert DEFAULT_LAYOUT.resolve(cand()) == Destination("Movies", "Heat (1995)")
    assert DEFAULT_LAYOUT.resolve(cand(kind="series", year=None)) == Destination("Shows", "Heat")


def test_first_match_wins_and_sets_both_values(tmp_path):
    lay = layout(
        """
rules:
  - match: { category: "kids" }
    into: "Kids/{kind}"
  - match: { kind: movie }
    folder: "{title} [{year}]"
""",
        tmp_path,
    )
    assert lay.resolve(cand(category="NETFLIX KIDS")) == Destination("Kids/Movies", "Heat (1995)")
    assert lay.resolve(cand(category="TOP")) == Destination("Movies", "Heat [1995]")
    assert lay.resolve(cand(kind="series", category="TOP")) == Destination("Shows", "Heat (1995)")


@pytest.mark.parametrize(
    "match, hit, miss",
    [
        ("kind: series", dict(kind="series"), dict(kind="movie")),
        ('category: "^TOP"', dict(category="TOP MOVIES"), dict(category="NOT TOP")),
        ("category: [7, 8]", dict(category_id=8), dict(category_id=9)),
        ('title: "heat$"', dict(title="Heat"), dict(title="Heat 2")),
        ('tag: "^4k"', dict(tags=("EN", "4K-NF")), dict(tags=("EN",))),
        ("year_min: 1995", dict(year=1995), dict(year=1994)),
        ("year_max: 1995", dict(year=1995), dict(year=1996)),
        ("year_min: 1990", dict(year=1990), dict(year=None)),
        ("is_4k: true", dict(is_4k=True), dict(is_4k=False)),
        ("is_4k: false", dict(is_4k=False), dict(is_4k=True)),
    ],
)
def test_each_condition(match, hit, miss, tmp_path):
    lay = layout(f"rules:\n  - match: {{ {match} }}\n    into: X\n", tmp_path)
    assert lay.resolve(cand(**hit)).library == "X"
    assert lay.resolve(cand(**miss)).library == "Movies"


def test_conditions_are_anded_and_empty_match_is_catch_all(tmp_path):
    lay = layout(
        """
rules:
  - match: { kind: movie, year_min: 2000 }
    into: New
  - match: {}
    into: Rest
""",
        tmp_path,
    )
    assert lay.resolve(cand(year=2001)).library == "New"
    assert lay.resolve(cand(year=1999)).library == "Rest"
    assert lay.resolve(cand(kind="series", year=2001)).library == "Rest"


def test_skip(tmp_path):
    lay = layout('rules:\n  - match: { title: "^WWE " }\n    skip: true\n', tmp_path)
    assert lay.resolve(cand(title="WWE Raw")) is None
    assert lay.resolve(cand(title="Heat")) == Destination("Movies", "Heat (1995)")


def test_empty_library_render_falls_back_to_kind(tmp_path):
    lay = layout('rules:\n  - match: {}\n    into: "{decade}"\n', tmp_path)
    assert lay.resolve(cand(year=None)).library == "Movies"
    assert lay.resolve(cand(year=1995)).library == "1990s"


def test_collision_dirs(tmp_path):
    lay = layout('collisions:\n  movies: ["/media/Movies"]\n  shows: ["/media/A", "/media/B"]\n', tmp_path)
    assert lay.movie_dirs == (Path("/media/Movies"),)
    assert lay.show_dirs == (Path("/media/A"), Path("/media/B"))


@pytest.mark.parametrize(
    "text, message",
    [
        ("rules: 3\n", "'rules' must be a list"),
        ("rules: false\n", "'rules' must be a list"),
        ("nope: 1\n", "unknown top-level keys: nope"),
        ("rules:\n  - 5\n", "rule 0: expected a mapping"),
        ("rules:\n  - match: {}\n", "rule 0: needs at least one of into, folder, skip"),
        ("rules:\n  - match: {}\n    into: X\n    bogus: 1\n", "rule 0: unknown keys: bogus"),
        ("rules:\n  - match: { nope: 1 }\n    into: X\n", "rule 0 'match': unknown keys: nope"),
        ("rules:\n  - match: []\n    into: X\n", "rule 0 'match': expected a mapping"),
        ("rules:\n  - match: { kind: film }\n    into: X\n", "rule 0 'kind': must be movie or series"),
        ('rules:\n  - match: { title: "(" }\n    into: X\n', "rule 0 'title': invalid regex"),
        ("rules:\n  - match: { category: [1, x] }\n    into: X\n", "rule 0 'category': must be a regex string or a list of integer ids"),
        ("rules:\n  - match: { year_min: soon }\n    into: X\n", "rule 0 'year_min': must be an integer"),
        ("rules:\n  - match: { is_4k: yes please }\n    into: X\n", "rule 0 'is_4k': must be true or false"),
        ("rules:\n  - match: {}\n    skip: 1\n", "rule 0 'skip': must be true or false"),
        ("rules:\n  - match: {}\n    into: \"\"\n", "rule 0 'into': must be a non-empty string"),
        ('rules:\n  - match: {}\n    into: "{nope}"\n', "rule 0 'into': unknown template variable {nope}"),
        ('rules:\n  - match: {}\n    folder: "{title}/{year}"\n', "rule 0 'folder': must not contain /"),
        ("collisions: []\n", "'collisions' must be a mapping"),
        ("collisions: { films: [] }\n", "'collisions': unknown keys: films"),
        ("collisions: { movies: /media }\n", "'collisions.movies' must be a list of paths"),
        ("collisions: { movies: false }\n", "'collisions.movies' must be a list of paths"),
    ],
)
def test_validation_errors(text, message, tmp_path):
    with pytest.raises(ConfigError, match=re.escape(message)):
        layout(text, tmp_path)


def test_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        layout("rules: [\n", tmp_path)
