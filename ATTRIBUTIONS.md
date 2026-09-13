# Attributions

This repository is configuration and a small sidecar built on top of the
following projects. Each is used unmodified under its own licence; nothing here
is affiliated with or endorsed by them.

## Services run by `compose.yaml`

| Project | Use here | Licence |
| --- | --- | --- |
| [Jellyfin](https://jellyfin.org) (`jellyfin/jellyfin`) | Media server | GPL-2.0 |
| [Gluetun](https://github.com/qdm12/gluetun) (`qmcgaw/gluetun`) | VPN client container, firewall and DNS for the app containers | MIT |
| [Tailscale](https://tailscale.com) (`tailscale/tailscale`) | Tailnet identity and HTTPS reverse proxy (Tailscale Serve) | BSD-3-Clause |
| [WireGuard](https://www.wireguard.com) | Tunnel protocol, via the Linux kernel module | GPL-2.0 |
| [Python](https://www.python.org) (`python:3.12-slim`) | Base image for the sidecar | PSF-2.0 |

## Host tooling

| Project | Use here | Licence |
| --- | --- | --- |
| [Podman](https://podman.io) | Rootless container runtime | Apache-2.0 |
| [podman-compose](https://github.com/containers/podman-compose) | Runs `compose.yaml` | GPL-2.0 |
| [uv](https://github.com/astral-sh/uv) | Runs the sidecar on the host for `make xtream-categories` | MIT / Apache-2.0 |

## `xtream-sync` Python dependencies

| Package | Licence |
| --- | --- |
| [requests](https://github.com/psf/requests) | Apache-2.0 |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |
| [urllib3](https://github.com/urllib3/urllib3) (via requests) | MIT |
| [certifi](https://github.com/certifi/python-certifi) (via requests) | MPL-2.0 |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) (via requests) | MIT |
| [idna](https://github.com/kjd/idna) (via requests) | BSD-3-Clause |
| [pytest](https://github.com/pytest-dev/pytest) (dev only) | MIT |
| [setuptools](https://github.com/pypa/setuptools) (build only) | MIT |

## External services and data

- **Windscribe** supplies the WireGuard endpoint. It is a paid service; no
  Windscribe software or credentials are included in this repository.
- **Xtream Codes API**: the sidecar speaks the Xtream-compatible provider API.
  No provider details are included; supply your own in `.env`.
- **TMDb**: the sidecar writes TMDb IDs it receives from the provider so that
  Jellyfin can match metadata. Metadata itself is fetched by Jellyfin. This
  project uses TMDb data but is not endorsed or certified by TMDb.

## Branding

The AFTERGLOW theme in `branding/afterglow/` was produced with AI image
generation and written for this instance; the prompts used are recorded in its
README. The stylesheet targets Jellyfin Web 10.11 selectors and contains no
third-party assets. The Jellyfin name and logo belong to the Jellyfin project.
