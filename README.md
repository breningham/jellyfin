# Jellyfin on the tailnet

Jellyfin plus its own Tailscale identity, run with rootless Podman via
`podman-compose`. Tailscale Serve terminates TLS with the node's MagicDNS
certificate and reverse-proxies `https://jellyfin.tail86146.ts.net` (port 443)
to Jellyfin. Userspace networking means no `/dev/net/tun` or extra
capabilities are needed. The local `http://127.0.0.1:8096` binding remains for
LAN use.

## Prerequisites

- Media root owned by you, outside `$HOME` (systemd-homed's idmapped mount
  cannot hold files owned by container users):

  ```sh
  sudo mkdir -p /opt/media/Movies /opt/media/Shows
  sudo chown -R "$USER:$USER" /opt/media
  ```

- HTTPS certificates enabled on the tailnet (admin console → DNS).
- `/dev/dri` is passed through for VAAPI transcoding on the AMD GPUs.

## First enrollment

Create a one-time, reusable auth key in the Tailscale admin console and put it
in `.env` (ignored by Git):

```dotenv
TS_AUTHKEY=tskey-auth-your-key-here
```

Then:

```sh
make up
make ts-status
```

The state volume `jellyfin_tailscale_state` keeps the node identity, and
`TS_AUTH_ONCE=true` makes later starts reuse it, so `TS_AUTHKEY` can be removed
from `.env` afterwards. The first HTTPS request triggers ACME issuance and can
take ~30s.

If the tailnet policy requires tags, add `TS_EXTRA_ARGS=--advertise-tags=tag:container`
to `.env` for the enrollment start only.

## First-run setup (in the web UI)

Open `https://jellyfin.tail86146.ts.net` (or `http://127.0.0.1:8096`) and run
the wizard, then:

1. **Libraries** — add `/media/Movies` (Movies) and `/media/Shows` (Shows).
2. **Dashboard → Networking → Known proxies** — add the Compose network subnet
   (`10.89.1.0/24` here; confirm with `podman network inspect jellyfin_jellyfin`)
   so forwarded client addresses are trusted.
3. **Dashboard → Playback → Transcoding** — hardware acceleration: VAAPI,
   device `/dev/dri/renderD128` (RX 7900 dGPU; `renderD129` is the Raphael
   iGPU — verify with `ls -l /dev/dri/by-path` and `lspci`, the numbering can
   change across boots).

## Published image and licence

Every push to `main` and every `v*` tag builds `xtream-sync` for amd64 and
arm64 and pushes it to `ghcr.io/breningham/jellyfin-xtream-sync` (tags
`latest`, `main`, `sha-…`, and the semver parts of a release tag). The
workflow runs the test suite first. `compose.yaml` references that image but
keeps `build:`, so `make up` still builds from source locally.

The repository is MIT licensed; see [LICENSE](LICENSE).

## Attributions

Third-party projects, images and libraries this stack is built on are listed
with their licences in [ATTRIBUTIONS.md](ATTRIBUTIONS.md).

## Branding

`branding/afterglow/` holds the AFTERGLOW theme: a splash image, a custom
stylesheet for Jellyfin Web 10.11, and previews. Apply it via Dashboard →
Branding; see `branding/afterglow/README.md` for the steps and palette.

## VPN (Gluetun + Windscribe)

Jellyfin and `xtream-sync` share the `gluetun` container's network namespace
(`network_mode: service:gluetun`), so every outbound connection they make,
IPTV streams and metadata included, leaves through a Windscribe WireGuard
tunnel. Gluetun's firewall is the kill switch: with the tunnel down, fresh
requests from both containers fail rather than falling back to the home
connection. Tailscale stays on the bridge network and proxies to
`gluetun:8096`; local media and the tailnet URL keep working during a tunnel
outage. Containers are deliberately not grouped in one pod (`x-podman.in_pod:
false`), since a pod would drag Tailscale into the VPN namespace too.

Setup:

1. Generate a WireGuard config at <https://windscribe.com/getconfig/wireguard>
   and save it as `config/windscribe/wg0.conf` (git-ignored, `chmod 600`).
2. Gluetun's custom provider needs a numeric `Endpoint`. Resolve the hostname
   Windscribe gives you and replace it with the IPv4 address, keeping the port.
   The `DNS` and `AllowedIPs` lines are ignored; Gluetun runs its own resolver
   on `127.0.0.1`, which the two containers use via `config/vpn/resolv.conf`.
3. `make up`. Gluetun must report healthy before Jellyfin and the sidecar start.

Check it with `podman logs jellyfin_gluetun_1` (look for the public IP line)
and `podman exec jellyfin_jellyfin_1 curl -4 https://api.ipify.org`, which
should print the Windscribe exit address.

Caveats: the endpoint is pinned, so if Windscribe retires that server the
tunnel needs a fresh config rather than switching location by itself. If the
`gluetun` container is ever recreated (image bump, env change), recreate
`jellyfin` and `xtream-sync` as well so they join the new namespace; a plain
`podman-compose down` (without `-v`) followed by `make up` does this.

## IPTV video-on-demand and series (xtream-sync)

The `xtream-sync` sidecar turns an Xtream-compatible IPTV provider's VOD and
series catalogues into `.strm` files in its own `iptv_library` volume, which
Jellyfin mounts at `/iptv` (read-write, so it can save artwork beside them), with a `.nfo` carrying the TMDb ID
beside each title, so Jellyfin treats them as normal libraries with full
metadata.

1. Put `XTREAM_HOST`, `XTREAM_USERNAME`, `XTREAM_PASSWORD` and a Jellyfin
   `JELLYFIN_API_KEY` in `.env` (see `.env.example`).
2. Run `make xtream-categories` to write `config/xtream/categories.yaml` from your
   provider's catalogue (git-ignored; `categories.example.yaml` shows the format),
   then uncomment the categories you want. Edits are picked up on
   the next run.
3. `make up`, then `make xtream-logs` to watch the first pass. The first run
   fetches episode lists for every series and can take 10–20 minutes; later
   runs only re-fetch series whose `last_modified` changed.
4. **Dashboard → Libraries** — open the Movies library and add `/iptv/Movies`
   as a second folder; add `/iptv/Shows` to the Shows library. Jellyfin shows
   one merged library each; `/opt/media` on the host stays untouched.

The sidecar runs as your own uid/gid inside its container (`PUID`/`PGID`,
exported by `make` from `id`), so everything it writes into the
`iptv_library` volume is owned by you. It runs every `XTREAM_SYNC_INTERVAL`
seconds (default 6 h). It only
ever deletes files it wrote itself, tracked in the `jellyfin_xtream_state`
volume. If the provider is down or every category comes back empty, the run
aborts and nothing on disk changes. A Jellyfin rescan is only requested when a
run changed files, and never while a scan is already in progress (Jellyfin
would cancel and restart it). `make xtream-sync-once` runs a single pass
in a throwaway container. Titles that appear in several categories are
collapsed to a single entry by TMDb ID, preferring a 4K entry and then the
newest one.

`config/xtream/categories.yaml` lists every category the provider offers, with
the enabled ones uncommented. `make xtream-categories` refreshes that list from
the provider while keeping the current selection.

### Layout rules

`config/xtream/layout.yaml` is optional; the shipped copy has no rules, but
does enable collision skipping against `/media/Movies` and `/media/Shows`.
The file decides which folder each title lands in and how its folder is
named; it documents the rule format. Extra destinations (say `Kids/Movies`)
are added to a Jellyfin library the same way as `/iptv/Movies`. Titles whose
folder name matches one under `/opt/media/Movies` or `/opt/media/Shows`, or
whose `movie.nfo`/`tvshow.nfo` there carries the same TMDb ID, are skipped,
so your own copy wins. `make xtream-check-layout` validates the file and
prints where the first titles of each category would go. Changing a rule
moves the affected files with their timestamps intact, so Jellyfin does not
refetch their metadata.

### Migrating from /opt/media/IPTV

If you previously curated IPTV content by hand under `/opt/media/IPTV`,
switch it over to the sidecar's own `iptv_library` volume:

1. After `make up`, run `make xtream-sync-once`. The summary line shows
   `+0` and a large `=N` restored count, because the manifest is kept and
   every file is written fresh into the empty volume.
2. In Jellyfin, add `/iptv/Movies` to the Movies library and `/iptv/Shows`
   to the Shows library, then remove the old `IPTV Movies` and `IPTV Shows`
   libraries.
3. In Dashboard → Live TV, change the M3U tuner path to `/iptv/live.m3u`
   and the XMLTV guide path to `/iptv/guide.xml`.
4. Run Scan All Libraries. Jellyfin fetches metadata for every IPTV title
   once, since they are new items to it.
5. Delete `/opt/media/IPTV` by hand; the sidecar never touches it again.

### Live TV

Categories under `live:` in `config/xtream/categories.yaml` become `live.m3u`
(channel list with logos, groups and stable channel numbers) and `guide.xml`
(the provider's XMLTV guide filtered to those channels; the provider only
publishes about three days, so it is refreshed every run) in the
`iptv_library` volume. Jellyfin setup (existing tuners: see Migrating
above), in Dashboard → Live TV:

- Tuner Devices → Add → M3U Tuner, file `/iptv/live.m3u`, simultaneous
  stream limit 1 (the provider allows one connection).
- TV Guide Data Providers → Add → XMLTV, file `/iptv/guide.xml`.

After a run that changed either file the sidecar runs Jellyfin's "Refresh
Guide" task, unless one is already running. `guide.xml` is rewritten on every
run (the provider regenerates it each time), but that alone only triggers the
guide refresh above, never a full library scan.

### Rewrites and provider changes

When the sidecar rewrites a tracked file (a host or provider change, or a
dedupe swap), it puts the file's previous modification time back, so Jellyfin
does not treat 180k STRM files as changed and re-fetch their metadata. Jellyfin
reads STRM/M3U contents only at play time, so this is safe. The one caveat:
if an `.nfo` ever changed meaningfully, Jellyfin would not notice without a
manual "Refresh metadata"; the sidecar's NFOs only carry the TMDb ID, which
does not change for a given folder.

Switching provider: update `XTREAM_HOST`/`XTREAM_USERNAME`/`XTREAM_PASSWORD`
in `.env`, run `make xtream-categories`, re-pick categories, `make xtream-redeploy`.

The provider allows one concurrent stream, so only one IPTV title can play at
a time across all clients.

## Verify

```sh
curl http://127.0.0.1:8096/health              # Healthy
curl https://jellyfin.tail86146.ts.net/health  # Healthy, from any tailnet device
podman exec jellyfin_jellyfin_1 /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner \
  -init_hw_device vaapi=va:/dev/dri/renderD128 -f lavfi -i nullsrc -t 1 -f null -
podman exec jellyfin_xtream-sync_1 ls /output/Movies | head  # folders like "Title (Year)"
```

## Operations

- `make down` / `make up` — stop and start both services.
- `podman-compose stop tailscale` — drop the tailnet
  endpoint without affecting Jellyfin.
- Upgrade: bump the image tags in `compose.yaml`, then `make up`.
- After changing the `xtream-sync` code: `make xtream-redeploy` rebuilds its
  image and recreates only that container (`make up` does not recreate a
  container whose image changed but whose config did not).
