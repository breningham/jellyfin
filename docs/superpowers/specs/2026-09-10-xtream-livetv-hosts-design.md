# Xtream sync pass 2 — Live TV, mtime preservation, multi-host — design

Date: 2026-09-10. Extends `2026-09-10-xtream-strm-sync-design.md`; everything
there still holds unless amended here.

## Goals

1. Expose a small set of the provider's live channels through Jellyfin's
   native Live TV (M3U tuner + XMLTV guide), replacing the removed plugin.
2. Make URL-only rewrites of STRM/M3U files invisible to Jellyfin by preserving
   file modification times, so a host or provider change, or a dedupe swap,
   never triggers a full metadata refresh.
3. Accept several provider hostnames (the provider publishes mirrors) and use
   the healthiest one, with hysteresis so the choice does not flap.

Out of scope: play-time failover (a redirect service) and multiple providers.

## 1. Live TV

### Configuration

`categories.yaml` gains a third list:

```yaml
live:
  - 1141  # UK| GENERAL
  - 1145  # UK| NEWS
  - 1142  # UK| KIDS
  - 1143  # UK| DOCUMENTARY
```

`--dump-categories` renders a `live:` section listing every live category
(917 today), commented out except the enabled ones. `load_categories` returns
`Categories(vod, series, live)`; a file with only `live:` is valid.

### Data flow (added to each run)

1. `get_live_categories` for names; `get_live_streams&category_id=N` per
   selected category, in config order.
2. Drop header pseudo-channels: any stream whose name starts with `#`.
3. Build the channel list: `Channel(stream_id, name, epg_id | None, logo |
   None, group)`. `name` is cleaned (below); `group` is the cleaned category
   name (`clean_name` from `categories_template`).
4. Fetch the provider XMLTV (`{host}/xmltv.php?username=&password=`) via the
   client (so errors are scrubbed). Stream-parse it, keeping `<channel>`
   elements whose `id` is in the selected EPG ids and `<programme>` elements
   whose `channel` is in that set. Write a well-formed `<tv>` document.
5. Add two files to the desired set, tracked in the manifest like all others:
   - `live.m3u`
   - `guide.xml`
6. After writing, if either file changed, request Jellyfin's guide refresh
   (scheduled task key `RefreshGuide`) with the same guard as the library
   refresh: skip if that task is already running; skip if nothing changed.

Per-kind abort rule applies: `live` selected but zero channels collected →
`RunAborted`, no files touched.

Guide failure handling: if the XMLTV download or parse fails, log a warning
and carry the previous `guide.xml` forward unchanged (read from the output dir
if present; if absent, omit `guide.xml` from the desired set). The channels
still update. A guide failure never aborts the run.

### M3U format

```
#EXTM3U
#EXTINF:-1 tvg-id="BBC1.uk" tvg-chno="1" tvg-name="BBC 1" tvg-logo="https://…" group-title="UK| GENERAL",BBC 1
http://HOST/live/USER/PASS/123.ts
```

- `tvg-chno` is sequential from 1 in output order (config order of
  categories, provider order within a category), so channel numbers are
  stable between runs unless the selection changes.
- Attributes are omitted when empty (no `tvg-id=""`). Attribute values have
  `"` replaced by `'`.
- Stream URL: `{host}/live/{user}/{pass}/{stream_id}.ts`, credentials
  percent-encoded like the other URLs.

### Channel name cleaning (`clean_channel_name`)

Applied to the provider name, in order:
- strip a leading `XX: ` / `XX| ` country tag (2–3 upper-case letters);
- remove quality/decoration tokens as whole words: `HEVC`, `HD`, `FHD`, `UHD`,
  `SD`, `4K`, `8K`, `ᴴᴰ`, `◉`, `⁴ᴷ`, and any superscript/modifier characters;
- collapse whitespace, trim.
`UK: BBC 1 HEVC HD` → `BBC 1`; `UK: BEIN SP⚽RTS 1 ENGLISH ᴴᴰ ◉` → `BEIN
SP⚽RTS 1 ENGLISH`. If cleaning leaves an empty string, keep the original.

### Jellyfin setup (one-off, via API, done in the verification task)

- Tuner: `POST /LiveTv/TunerHosts` with `Type: "m3u"`, `Url:
  "/media/IPTV/live.m3u"`, `FriendlyName: "Xtream"`, `TunerCount: 1`
  (the provider allows one connection), `AllowHWTranscoding: true`.
- Listings: `POST /LiveTv/ListingProviders` with `Type: "xmltv"`, `Path:
  "/media/IPTV/guide.xml"`, `EnableAllTuners: true`.
- Then run the `RefreshGuide` task once.

The README documents these as dashboard steps too (Live TV → Tuner Devices /
TV Guide Data Providers).

## 2. mtime preservation

In `writer.apply_diff`, when a path is in `diff.updated` (tracked, content
changed) the file is rewritten as now, and then its modification time is reset
to the value it had before the write (`os.utime` with the previous `st_mtime`
and `st_atime`). Applies to every file the writer updates, including `.nfo`,
`live.m3u` and `guide.xml`.

Rationale: Jellyfin detects change by modification time and reads STRM
contents only at play time; NFO changes we make are TMDb-id-only and never
change for the same folder. Consequence: if a `.nfo` did change meaningfully
Jellyfin would not re-read it until a manual refresh — acceptable, and noted
in the README.

`guide.xml` is the exception: Jellyfin's XMLTV provider reads it during guide
refresh regardless of mtime, so preserving mtime is harmless there too; no
special case needed.

New/added files keep the current time (they are new to Jellyfin anyway).

## 3. Multiple hosts

### Configuration

`XTREAM_HOST` accepts a comma-separated list of base URLs, e.g.
`http://a.example,http://b.example:8080`. Whitespace around entries is
ignored. A single URL behaves exactly as today.

### Selection (`hosts.py`, pure logic + one probe function)

At the start of each run:
1. Probe every host: `GET {host}/player_api.php?username=&password=` with a
   10 s timeout; healthy if HTTP 200, JSON, and `user_info.auth == 1`. Record
   elapsed time. Probes run sequentially (few hosts, simple).
2. Read the previously chosen host from `/state/host.json` (absent on first
   run).
3. Choose:
   - if the previous host is healthy and no other healthy host is faster by
     at least 30 %, keep the previous host;
   - otherwise choose the fastest healthy host;
   - if none is healthy, raise `ProviderError` → the run aborts as today.
4. Persist the choice to `/state/host.json` and log
   `host check: a 120ms ok, b 95ms ok -> using a (kept)`.

The chosen host is used for all API calls in that run and in every URL written
(STRM, M3U). A host change therefore rewrites every URL-bearing file; with
mtime preservation this is invisible to Jellyfin.

`Credentials.host` becomes the chosen host; the list lives in `Settings.hosts:
tuple[str, ...]`. `--dump-categories` also runs the selection.

Errors: probe failures are logged at INFO with the scrubbed reason; only
"no healthy host" is an error.

## Error-handling additions

| Situation | Behaviour |
|---|---|
| No host healthy | Abort run (ProviderError), retry next interval |
| Live category empty / unknown | Warn, continue (existing rule) |
| All selected live categories empty | Abort run (per-kind rule) |
| XMLTV download/parse fails | Warn; keep previous guide.xml; channels still written |
| Guide refresh task already running | Skip, log, not a failure |

## Testing

- `livetv.py`: name cleaning table; M3U rendering (attributes omitted when
  empty, quoting, tvg-chno sequence, header rows dropped); XMLTV filtering on a
  small inline sample with 3 channels and 6 programmes → only the selected
  ones remain and the output re-parses.
- `writer.py`: updated file keeps its previous mtime; added file does not.
- `hosts.py`: keep-previous-within-30%, switch-when-faster, switch-when-
  previous-unhealthy, first-run-fastest, none-healthy raises; probe function
  tested with a fake session.
- `config.py`: `XTREAM_HOST` list parsing; `live:` list parsing.
- `sync.py`: live channels written, guide filtered, header rows dropped,
  guide failure keeps old guide, live-all-empty aborts, guide refresh called
  only on change.
- Manual: `make xtream-sync-once`; inspect `/opt/media/IPTV/live.m3u` and
  `guide.xml`; API tuner + listings setup; `RefreshGuide`; channels visible in
  the web UI Live TV guide; play one channel.

## Files

New: `xtream_sync/livetv.py`, `xtream_sync/hosts.py`, tests.
Modified: `client.py` (xmltv fetch, live_categories/live_streams, live_url),
`config.py`, `categories_template.py`, `catalog.py` (nothing), `writer.py`,
`jellyfin.py` (`refresh_guide`, task-state helper generalised),
`sync.py`, `__main__.py`, `config/xtream/categories.yaml`, `.env.example`,
`README.md`.
