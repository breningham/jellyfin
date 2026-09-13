"""Sanitised samples of provider responses, shared by tests."""

VOD_ITEM = {
    "num": 1,
    "name": "EN - Wimbledon (2004)",
    "stream_type": "movie",
    "stream_id": 2145896,
    "stream_icon": "https://image.tmdb.org/t/p/w600/agi.jpg",
    "rating": "6.23",
    "tmdb": "11823",
    "added": "1788823920",
    "is_adult": 0,
    "category_id": "119",
    "category_ids": [119],
    "container_extension": "mkv",
    "custom_sid": None,
    "direct_source": "",
}

SERIES_ITEM = {
    "num": 1,
    "name": "4K-NF - The Gentlemen (2024) (GB)",
    "series_id": 50012,
    "cover": "https://example/cover.jpg",
    "plot": "When aristocratic Eddie inherits the family estate...",
    "releaseDate": "2024-03-07",
    "last_modified": "1788879650",
    "tmdb": "236235",
    "category_id": "427",
    "category_ids": [427],
}

SERIES_INFO = {
    "seasons": [],
    "info": {"name": "4K-NF - The Gentlemen (2024) (GB)", "tmdb": "236235"},
    "episodes": {
        "1": [
            {
                "id": "2141519",
                "episode_num": 1,
                "title": "4K-NF - The Gentlemen (2024) (GB) - S01E01 - Refined Aggression",
                "container_extension": "mkv",
                "season": 1,
                "info": {"air_date": "2024-03-07", "rating": 7.7},
            },
            {
                "id": "2141520",
                "episode_num": 2,
                "title": "4K-NF - The Gentlemen (2024) (GB) - S01E02 - Tackle Tommy Woo Woo",
                "container_extension": "mkv",
                "season": 1,
                "info": {},
            },
        ],
        "2": [
            {
                "id": "2141600",
                "episode_num": 1,
                "title": "S02E01",
                "container_extension": "mp4",
                "season": 2,
                "info": {},
            }
        ],
    },
}
