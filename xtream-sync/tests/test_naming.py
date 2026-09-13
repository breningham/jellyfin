import pytest

from xtream_sync.naming import (
    assign_folders,
    clean_title,
    dedupe_folders,
    episode_path,
    movie_paths,
    safe_component,
    show_nfo_path,
    split_title,
    title_folder,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("EN - Wimbledon (2004)", ("Wimbledon", 2004)),
        ("4K-NF - The Gentlemen (2024) (GB)", ("The Gentlemen", 2024)),
        ("NF - EN - Stranger Things (2016)", ("Stranger Things", 2016)),
        ("Plain Title", ("Plain Title", None)),
        ("  Spaced   Out  (1999) ", ("Spaced Out", 1999)),
        ("The Office (US) (2005)", ("The Office (US)", 2005)),
        ("Kill Bill - Vol. 1 (2003)", ("Kill Bill - Vol. 1", 2003)),
        ("EN - Us (2019) (US)", ("Us", 2019)),
        ("M - Eine Stadt sucht einen Mörder (1931)", ("M - Eine Stadt sucht einen Mörder", 1931)),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Face/Off", "Face-Off"),
        ('What: Is "This"?', "What- Is -This--"),
        ("  dots... ", "dots"),
        ("", "untitled"),
        ("A  B", "A B"),
    ],
)
def test_safe_component(raw, expected):
    assert safe_component(raw) == expected


def test_title_folder_with_and_without_year():
    assert title_folder("Wimbledon", 2004) == "Wimbledon (2004)"
    assert title_folder("Plain", None) == "Plain"
    assert title_folder("Face/Off", 1997) == "Face-Off (1997)"


def test_assign_folders_suffixes_collisions_in_id_order():
    entries = [(30, "Dune", 2021), (10, "Dune", 2021), (20, "Other", None)]
    assert assign_folders(entries) == {
        10: "Dune (2021)",
        30: "Dune (2021) [xtream-30]",
        20: "Other",
    }


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("EN - Wimbledon (2004)", ("Wimbledon", 2004, ("EN",))),
        ("4K-NF - The Gentlemen (2024) (GB)", ("The Gentlemen", 2024, ("4K-NF",))),
        ("NF - EN - Stranger Things (2016)", ("Stranger Things", 2016, ("NF", "EN"))),
        ("Plain Title", ("Plain Title", None, ())),
        ("M - Eine Stadt sucht einen Mörder (1931)", ("M - Eine Stadt sucht einen Mörder", 1931, ())),
    ],
)
def test_split_title_returns_tags(raw, expected):
    assert split_title(raw) == expected


def test_dedupe_folders_suffixes_later_ids():
    assert dedupe_folders([(30, "Dune (2021)"), (10, "Dune (2021)"), (20, "Other")]) == {
        10: "Dune (2021)",
        30: "Dune (2021) [xtream-30]",
        20: "Other",
    }


def test_movie_paths():
    assert movie_paths("Movies", "Wimbledon (2004)") == (
        "Movies/Wimbledon (2004)/Wimbledon (2004).strm",
        "Movies/Wimbledon (2004)/movie.nfo",
    )
    assert movie_paths("Kids/Movies", "Up (2009)") == (
        "Kids/Movies/Up (2009)/Up (2009).strm",
        "Kids/Movies/Up (2009)/movie.nfo",
    )


def test_show_paths():
    assert show_nfo_path("Shows", "The Gentlemen (2024)") == "Shows/The Gentlemen (2024)/tvshow.nfo"
    assert episode_path("Shows", "The Gentlemen (2024)", 1, 3) == (
        "Shows/The Gentlemen (2024)/Season 01/The Gentlemen (2024) S01E03.strm"
    )
    assert episode_path("Kids/Shows", "X", 12, 105) == "Kids/Shows/X/Season 12/X S12E105.strm"
