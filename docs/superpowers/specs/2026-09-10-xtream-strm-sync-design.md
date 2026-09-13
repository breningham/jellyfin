# Xtream STRM sync — design

Date: 2026-09-10

## Goal

Expose an Xtream-compatible IPTV provider's video-on-demand (VOD) and series
catalogues to Jellyfin as ordinary Movies and Shows libraries, instead of the
second-class "Channels" view the Jellyfin Xtream plugin provides. This gives
TMDb metadata, artwork, continue-watching and next-up in every client.

Live TV is out of scope for this pass. It will be a second pass using
Jellyfin's native M3U tuner and XMLTV guide.

## Approach

A sidecar container, `xtream-sync`, in the existing Compose project. On a
fixed interval it queries the provider's `player_api.php`, writes one `.strm`
file per movie and per episode into `${MEDIA_DIR}/IPTV/{Movies,Shows}`, writes
a minimal `.nfo` beside each title carrying the provider's TMDb ID, removes
files it previously created that are no longer in the selection, and asks
Jellyfin to refresh its libraries.

Jellyfin already mounts `${MEDIA_DIR}` read-only at `/media`, so the generated
tree is visible at `/media/IPTV/Movies` and `/media/IPTV/Shows` with no
compose change on the Jellyfin side. The user adds those two paths as new
libraries of type Movies and Shows.

## Configuration

Environment (from `.env`, which is git-ignored):

| Variable | Required | Meaning |
|---|---|---|
| `XTREAM_HOST` | yes | Provider base URL, e.g. `http://host` (no path) |
| `XTREAM_USERNAME` | yes | Provider username |
| `XTREAM_PASSWORD` | yes | Provider password |
| `JELLYFIN_API_KEY` | no | If set, trigger a library refresh after each run |
| `XTREAM_SYNC_INTERVAL` | no | Seconds between runs, default `21600` (6 h) |
| `MEDIA_DIR` | no | Host media root, default `/opt/media` (already used by Jellyfin) |

Category selection lives in a committed file `config/xtream/categories.yaml`:

```yaml
vod:
  - 163  # EN - NEW RELEASE
  - 287  # EN - DRAMA
series:
  - 427  # NETFLIX SERIES
```

IDs are the provider's numeric category IDs. The initial file is seeded from
the 24 VOD and 13 series categories the user selected in the plugin. The file
is mounted read-only at `/config/categories.yaml` and re-read at the start of
every run, so edits take effect on the next run without a restart.

Missing required env vars, an unreadable config file, or a config file with
neither list fail fast at startup with a clear message.

## Container

- `xtream-sync/Dockerfile` on `python:3.12-slim`; installs `requests` and
  `pyyaml` only. Runs as the container's default root user. Under rootless
  Podman container root maps to the host user, so generated files under
  `/opt/media/IPTV` are owned by the user, matching the existing `/opt/media`
  convention and how the Jellyfin container's own volumes behave.
- Mounts: `${MEDIA_DIR}/IPTV` at `/output` read-write; named volume
  `jellyfin_xtream_state` at `/state`; `./config/xtream` at `/config` read-only.
- Joins the `jellyfin` network so it can reach `http://jellyfin:8096`.
- `restart: unless-stopped`; `depends_on: jellyfin`.
- Entrypoint loops: sync, log summary, sleep `XTREAM_SYNC_INTERVAL`, repeat.
  `xtream-sync --once` runs a single pass and exits, used by a Makefile target
  and for manual verification.

## Package layout

`xtream-sync/xtream_sync/`, small modules with one responsibility each:

- `client.py` — `XtreamClient` wrapping `player_api.php` calls:
  `vod_categories()`, `series_categories()`, `vod_streams(category_id)`,
  `series(category_id)`, `series_info(series_id)`, plus `movie_url(stream_id,
  ext)` and `episode_url(episode_id, ext)`. Uses a `requests.Session` with a
  60 s timeout and a stable User-Agent. Raises `ProviderError` on HTTP or
  JSON failure.
- `naming.py` — pure functions: `clean_title(raw) -> (title, year | None)`,
  `movie_paths(title, year, stream_id)`, `episode_paths(title, year, season,
  episode, stream_id)`, `safe_component(s)`.
- `catalog.py` — pure: parses provider JSON into `Movie` and `Show` records
  and builds the desired file set (`{relative_path: content}`).
- `writer.py` — manifest load/save, diff of desired set against the previous
  manifest, and applying it: writes changed files, deletes removed files,
  prunes empty directories.
- `series_cache.py` — per-series episode cache keyed by `last_modified`.
- `config.py` — settings from env and category IDs from YAML, with
  validation.
- `sync.py` — `run_once`: one full pass wiring the above together.
- `jellyfin.py` — `refresh_library(base_url, api_key)`: `POST
  /Library/Refresh` with the `Authorization: MediaBrowser Token="..."` header.
- `__main__.py` — CLI: loads config, wires the modules, runs once or loops.

Only `sync.py` and `__main__.py` combine network and filesystem side effects;
`naming.py`, `catalog.py` and the diff logic in `writer.py` are pure and
unit-tested.

## Data flow per run

1. Load `categories.yaml`.
2. For each VOD category: `get_vod_streams&category_id=N`. Each item yields
   one movie from `name`, `stream_id`, `container_extension`, `tmdb`.
3. For each series category: `get_series&category_id=N`. For each series,
   compare `last_modified` with the cached copy in `/state/series/<id>.json`.
   If unchanged, use the cached episode list; otherwise call
   `get_series_info&series_id=N` and cache the result. Episodes come from
   `episodes[season][]` with `id`, `episode_num`, `container_extension`.
4. Build the desired set: for every movie `Movies/<Title (Year)>/<Title
   (Year)>.strm` and `Movies/<Title (Year)>/movie.nfo`; for every episode
   `Shows/<Title (Year)>/Season NN/<Title (Year)> SNNENN.strm` and one
   `Shows/<Title (Year)>/tvshow.nfo` per series.
5. Diff against `/state/manifest.json` (a map of relative path to a content
   hash). Write new or changed files, delete paths that were in the old
   manifest but not the new set, remove directories left empty under
   `/output`. Never touch a path that is not in the old manifest.
6. Save the new manifest atomically (write temp, rename).
7. If `JELLYFIN_API_KEY` is set, call the refresh endpoint. A failure here is
   logged and does not fail the run.
8. Log: movies added/updated/removed, episodes added/updated/removed, series
   refetched vs cached, and every selected category that returned zero items
   or was absent from the provider's category list.

A category that returns zero items is treated as a warning, not an empty
selection: if *every* category returns zero, or any provider call fails, the
run aborts before step 5 and the manifest and files are left untouched. This
prevents a provider outage from wiping the libraries.

A per-series `get_series_info` failure logs a warning, keeps that series'
previous files (by carrying its cached entry forward), and continues.

## Naming rules

`clean_title(raw)`:

- Strip a leading provider tag: a run of letters, digits, `+`, `-`, `.`
  followed by ` - `, applied at most twice (handles `EN - ` and `4K-NF - `).
- Strip a trailing parenthesised 2–3 letter country tag such as `(GB)`.
- Extract a trailing `(YYYY)` as the year and remove it from the title.
- Collapse whitespace.

`safe_component(s)` replaces `/ \ : * ? " < > |` with `-` and trims dots and
spaces from the ends. The folder and file stem are `Title (Year)` when a year
is known, else `Title`.

Collisions: if two different movies or series clean to the same folder, the
provider ID is appended as ` [xtream-<id>]` to the folder of the second and
subsequent ones. Ordering is by provider ID so the assignment is stable
across runs.

NFO content is the minimal Jellyfin/Kodi form:

```xml
<movie><tmdbid>11823</tmdbid></movie>
<tvshow><tmdbid>236235</tmdbid></tvshow>
```

When `tmdb` is empty or not numeric, no NFO is written and Jellyfin falls back
to matching on the cleaned name and year.

STRM content is the provider stream URL:
`{host}/movie/{user}/{pass}/{stream_id}.{ext}` and
`{host}/series/{user}/{pass}/{episode_id}.{ext}`. The credentials therefore
appear inside every STRM file; the tree is under `/opt/media` which is owned
by the user with mode 755. This is the same exposure as the plugin's config
file and is acceptable for a single-user host.

## Error handling summary

| Situation | Behaviour |
|---|---|
| Required env var missing | Exit 2 at startup with message |
| Config unreadable / no categories | Exit 2 at startup with message |
| Provider HTTP/JSON error on categories or lists | Abort run, no file changes, retry next interval |
| All categories empty | Abort run, no file changes |
| One category empty | Warn, continue |
| `series_info` fails for one series | Warn, keep previous files for it, continue |
| Jellyfin refresh fails | Warn, run still counts as success |
| Write/delete I/O error | Abort run; manifest not saved, so next run retries |

## Testing

- `pytest` under `xtream-sync/tests/`, no network.
- `naming.py`: table-driven tests for prefix and country stripping, year
  extraction, unsafe characters, and collision suffixing.
- `writer.py`: build the desired set from recorded provider JSON fixtures
  (sanitised samples of `get_vod_streams`, `get_series`, `get_series_info`);
  diff against a manifest; run against a `tmp_path` and assert files created,
  updated, deleted, empty dirs pruned, untracked files untouched.
- `client.py`: URL construction and error mapping with a stubbed session.
- Manual: `make xtream-sync-once`, then check `/opt/media/IPTV` and the
  Jellyfin library scan.

## Files

New:

- `xtream-sync/Dockerfile`, `xtream-sync/pyproject.toml`
- `xtream-sync/xtream_sync/{__init__,__main__,client,naming,catalog,writer,series_cache,config,sync,jellyfin}.py`
- `xtream-sync/tests/` with fixtures
- `config/xtream/categories.yaml`

Modified:

- `docker-compose.yaml`: add `xtream-sync` service and `jellyfin_xtream_state`
  volume
- `.env.example`: document the new variables
- `Makefile`: `xtream-sync-once` and `xtream-logs` targets
- `README.md`: the two new libraries, the config file, and the note that the
  Jellyfin Xtream plugin's VOD/Series toggles should be turned off (or the
  plugin removed) once the libraries are in place, to avoid duplicates
