# Xtream layout rules and merged libraries — design

Date: 2026-09-13

## Goal

Let the operator decide where `xtream-sync` puts each title, with a small
rule file, and keep the generated `.strm` tree out of the real media
directory. Jellyfin merges the two by having each library point at both a
real folder and a sidecar folder. Someone browsing `/opt/media` on the host
sees only their own files.

Two things change: where the sidecar's output lives, and how a title's
library folder and title folder are chosen. Everything else in the sidecar
(selection by category, dedupe by TMDb ID, the manifest and its
"only delete what I wrote" rule, live TV, the Jellyfin refresh) stays as it
is.

## Storage and mounts

A new named volume `iptv_library` replaces the `${MEDIA_DIR}/IPTV` bind
mount.

| Container | Mount | Mode | Purpose |
| --- | --- | --- | --- |
| `xtream-sync` | `iptv_library` at `/output` | rw | The generated tree. Unchanged path inside the container. |
| `xtream-sync` | `${MEDIA_DIR}` at `/media` | ro | Read the real libraries for collision checks. |
| `jellyfin` | `iptv_library` at `/iptv` | ro | Second folder for each library, plus the live TV files. |
| `jellyfin` | `${MEDIA_DIR}` at `/media` | ro | Unchanged. |

Jellyfin-side changes are made once by hand in the dashboard and documented
in the README:

- Movies library: add `/iptv/Movies`. Shows library: add `/iptv/Shows`.
- Any extra destination a rule creates (for example `/iptv/Kids/Movies`)
  is added as a folder on an existing library or as a new library.
- Live TV: the M3U tuner path becomes `/iptv/live.m3u` and the XMLTV guide
  path `/iptv/guide.xml`.
- The old `IPTV Movies` and `IPTV Shows` libraries are removed.

The first run after the switch writes the whole tree into the empty volume.
Series episode lists come from the `/state` cache, so this is minutes, not
the 10–20 minute first-ever run. Jellyfin then fetches metadata for every
IPTV title once, because to Jellyfin they are new items in a new folder.
After the libraries are switched, `/opt/media/IPTV` is deleted by hand; the
sidecar never touches it again.

The Makefile's `up` target stops creating `${MEDIA_DIR}/IPTV`.

## Rule file

`config/xtream/layout.yaml`, mounted with the rest of `config/xtream` at
`/config`. The file is optional. With no file, behaviour is exactly today's:
movies under `Movies/{title} ({year})`, shows under `Shows/{title} ({year})`,
no collision checks. It is a separate file from `categories.yaml` because
`make xtream-categories` regenerates that file wholesale.

```yaml
# Evaluated top to bottom; the first rule whose every condition holds wins.
rules:
  - match: { category: "KIDS" }
    into: "Kids/{kind}"
  - match: { category: "DOCU" }
    into: "Documentaries"
  - match: { kind: movie, tag: "4K" }
    folder: "{title} ({year}) [4K]"
  - match: { title: "^WWE " }
    skip: true

# Real library folders, as mounted in the sidecar. A title already present
# in one of these is skipped.
collisions:
  movies: ["/media/Movies"]
  shows: ["/media/Shows"]
```

### Rules

A rule is a mapping with a `match` block and at least one of `into`,
`folder`, `skip`. Every condition in `match` must hold. An empty or missing
`match` matches everything, so it can serve as a catch-all at the end.

| Condition | Type | Meaning |
| --- | --- | --- |
| `kind` | `movie` or `series` | Item kind. Applies to a show as a whole, never per episode. |
| `category` | string or list of ints | A string is a case-insensitive regular expression searched in the provider's category name. A list is a set of category IDs. |
| `title` | string | Case-insensitive regular expression searched in the cleaned title. |
| `tag` | string | Case-insensitive regular expression searched in each provider prefix tag the title cleaner strips (for example `EN`, `4K-NF`). Holds if any tag matches. |
| `year_min`, `year_max` | int | Inclusive bounds. An item with no year never matches either. |
| `is_4k` | bool | The existing 4K detection (item name or category name). |

Actions:

| Key | Default | Meaning |
| --- | --- | --- |
| `into` | `"{kind}"` | Library folder template, relative to `/output`. Renders to `Movies` or `Shows` by default. May contain `/`. |
| `folder` | `"{title} ({year})"` | Title folder template. Must render to a single path component. |
| `skip` | `false` | Drop the item. Ignores `into` and `folder`. |

When a rule sets only `into` or only `folder`, the other keeps its default.
Rules do not cascade: the first matching rule decides both values.

### Templates

Variables: `{title}`, `{year}`, `{tmdb}`, `{kind}` (`Movies` or `Shows`),
`{category}` (provider category name, made path-safe), `{letter}` (first
character of the title upper-cased, `#` when not a letter), `{decade}`
(`1990s`; empty without a year).

After substitution, a bracket pair whose inside is empty or only whitespace
is removed along with any space before it (`Heat ()` → `Heat`,
`Heat () [4K]` → `Heat [4K]`), runs of whitespace collapse, and each path
component goes through the existing `safe_component`. An unknown variable
is a validation error, reported with the rule's index. This also applies to
a title that itself contains an empty bracket pair such as `[]`: it is
removed even though the content is otherwise unchanged, so the file is
renamed, not recreated.

Duplicate title folders inside the same library folder are still suffixed
with `[xtream-<id>]` by `assign_folders`, now computed per library folder
rather than globally.

### Collisions

`collisions.movies` and `collisions.shows` list directories, as seen by the
sidecar. Both default to empty. Each directory is listed once per run with a
single `scandir`. A candidate is a collision when a subdirectory's name,
normalised by `safe_component` and lower-cased, equals the candidate's
rendered title folder normalised the same way, or when a `movie.nfo` or
`tvshow.nfo` directly inside such a subdirectory carries the same TMDb ID.
The `.nfo` check only runs for subdirectories whose name matched nothing,
and only reads files under 64 KiB. Collided items are skipped and counted in
the run summary log line.

A missing collision directory is a warning at run start, not an error, so
the stack still works before the mount is added.

### Validation

`layout.yaml` is parsed at the start of every run, like `categories.yaml`.
Any error (bad YAML, unknown key, wrong type, invalid regex, unknown template
variable, a `folder` that renders with a `/`) aborts that run with a
`ConfigError` naming the rule index and key, and nothing on disk changes.
The `--check-layout` flag validates the file, prints the resolved
destination for the first twenty titles of each enabled category using the
provider's category list, and exits, so a rule edit can be tried without a
sync.

## Engine changes

New pure module `xtream_sync/layout.py`:

- `Layout` (frozen dataclass): ordered `rules`, `collisions`.
- `load_layout(path) -> Layout`; returns the default layout when the file
  does not exist.
- `Destination` (frozen dataclass): `library: str`, `folder: str`.
- `Layout.resolve(item: Movie | Show, category: str) -> Destination | None`
  where `None` means skip. Tags come from a new `clean_title` return value
  (see below), category name from the existing `_category_names` mapping.
- `render(template, variables) -> str` with the tidy-up rules above.

`naming.py`:

- `clean_title` also returns the stripped prefix tags, as a tuple. Existing
  callers ignore the extra value.
- `movie_paths`, `show_nfo_path`, `episode_path` take `library: str` and
  `folder: str` instead of using `MOVIES_DIR`/`SHOWS_DIR`. The within-title
  layout (`{folder}/{folder}.strm`, `movie.nfo`, `tvshow.nfo`,
  `Season NN/{folder} SNNENN.strm`) does not change.

`catalog.py`:

- `Movie` and `Show` gain `category: str` and `tags: tuple[str, ...]`.
- `build_desired` takes a `resolve` callable and a `collides` callable
  alongside the URL functions. It groups kept items by library folder,
  assigns folders per group, and skips items that resolve to `None` or
  collide.

`writer.py`:

- Moves. Before applying a diff, entries removed and entries added with
  identical content are paired; each pair becomes an `os.replace` of the old
  path to the new one, creating parent directories, followed by the existing
  mtime restore. A rule edit that relocates titles therefore keeps their
  timestamps and Jellyfin does not treat them as changed. Content here is
  the file body (the stream URL, or the `.nfo` text), so a pairing is always
  the same title.
- Pruning empty parents stops at any directory that is a library folder of
  the current run's desired set, in addition to stopping at `/output`.

`sync.py` loads the layout, builds the collision index, passes the two
callables into `build_desired`, and adds `layout=<n rules>`, `skipped`,
`collided` and `moved` counts to the run summary line.

`compose.yaml`, `Makefile`, `.env.example` and the README change as described
under Storage and mounts.

## Error handling

- Rule file errors abort the run before any provider request; the next
  scheduled run retries, so a bad edit is corrected without restarting the
  container.
- A regex that fails at match time cannot happen: patterns are compiled at
  load.
- A collision directory that disappears mid-run is treated as empty for that
  run.
- Rename failures fall back to delete-plus-write for that file and are
  logged at warning level.

## Testing

Unit tests, all pure and filesystem-free except where noted:

- `layout`: each condition type alone and combined; first-match ordering;
  empty `match` catch-all; `skip`; defaults when only one of `into`/`folder`
  is set; every template variable; bracket tidy-up cases; validation errors
  for unknown keys, bad regex, unknown variable, folder rendering with `/`,
  each naming the rule index.
- `naming`: `clean_title` tag extraction for zero, one and two prefixes.
- `catalog.build_desired`: per-library folder assignment and suffixing;
  skipped and collided items absent from the desired set.
- collisions (tmp dirs): name match with differing case and unsafe
  characters, `.nfo` TMDb match, no match, missing directory.
- `writer` (tmp dirs): a moved file is renamed with its mtime intact; a
  changed-content file still follows the rewrite path; library roots survive
  pruning; rename failure falls back cleanly.
- `--check-layout` output shape against recorded fixtures.

The existing 187 tests keep passing. Manual check: `make xtream-sync-once`
into the new empty volume, confirm counts in the summary line, then switch
the Jellyfin library paths and confirm a title from each library plays.
