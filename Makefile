# Runtime: rootless podman via podman-compose. Jellyfin and its Tailscale
# endpoint share one Compose project.

export DBUS_SESSION_BUS_ADDRESS := unix:path=/run/user/$(shell id -u)/bus
COMPOSE = podman-compose
# xtream-sync runs as this uid/gid inside its container (see compose.yaml).
export PUID := $(shell id -u)
export PGID := $(shell id -g)
MEDIA_DIR ?= /opt/media

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) stop

status:
	@$(COMPOSE) ps

logs:
	@$(COMPOSE) logs -f --tail=50

ts-status:
	@$(COMPOSE) exec tailscale tailscale status
	@$(COMPOSE) exec tailscale tailscale serve status

xtream-sync-once:
	$(COMPOSE) run --rm xtream-sync --once

# Validate config/xtream/layout.yaml and show where sample titles would land.
xtream-check-layout:
	$(COMPOSE) run --rm xtream-sync --check-layout

xtream-logs:
	@$(COMPOSE) logs -f --tail=50 xtream-sync

# Rebuild the sidecar image and recreate only that container. `up` alone does
# not recreate a container whose image changed but whose config did not.
xtream-redeploy:
	$(COMPOSE) build xtream-sync
	$(COMPOSE) up -d --no-deps --force-recreate xtream-sync

# Rewrite config/xtream/categories.yaml with every provider category, keeping
# the current selection enabled. Runs on the host via uv; reads .env for creds.
xtream-categories:
	@set -a; . ./.env; set +a; \
	XTREAM_CATEGORIES=$(CURDIR)/config/xtream/categories.yaml \
	XTREAM_STATE_DIR=$$(mktemp -d) \
	uv run --project xtream-sync xtream-sync --dump-categories > config/xtream/categories.yaml.tmp \
	&& mv config/xtream/categories.yaml.tmp config/xtream/categories.yaml \
	&& grep -c '^  - ' config/xtream/categories.yaml | sed 's/^/enabled categories: /'
