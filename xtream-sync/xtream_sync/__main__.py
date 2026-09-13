"""Command-line entry point: run one sync pass, or loop forever."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from time import sleep

from .categories_template import render
from .client import Credentials, ProviderError, XtreamClient
from .config import Categories, ConfigError, load_categories, load_settings
from .hosts import select_host
from .layout import load_layout
from .series_cache import SeriesCache
from .sync import RunAborted, RunSummary, check_layout, run_once

log = logging.getLogger("xtream_sync")


def _log_summary(summary: RunSummary) -> None:
    d = summary.diff
    log.info(
        "sync complete: %d movies, %d shows, %d episodes, %d channels (%d programmes); "
        "files +%d ~%d -%d >%d =%d; layout %d rules, skipped %d, collided %d; series fetched %d, cached %d",
        summary.movies,
        summary.shows,
        summary.episodes,
        summary.channels,
        summary.programmes,
        len(d.added),
        len(d.updated),
        len(d.removed),
        len(d.moved),
        len(d.restored),
        summary.rules,
        summary.skipped,
        summary.collided,
        summary.series_fetched,
        summary.series_cached,
    )
    for label in summary.empty_categories:
        log.warning("category returned nothing: %s", label)
    if summary.refreshed is False:
        log.warning("jellyfin library refresh was not accepted")
    if summary.guide_refreshed is False:
        log.warning("jellyfin guide refresh was not accepted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xtream-sync")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument(
        "--dump-categories",
        action="store_true",
        help="print a categories.yaml listing every provider category, keeping the current selection enabled",
    )
    parser.add_argument(
        "--check-layout",
        action="store_true",
        help="validate layout.yaml and print where the first titles of each category would go",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr if (args.dump_categories or args.check_layout) else sys.stdout,
    )

    try:
        settings = load_settings(os.environ)
        if not args.dump_categories:
            load_categories(settings.categories_path)
            load_layout(settings.layout_path)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    base_client = XtreamClient(Credentials(settings.hosts[0], settings.username, settings.password))

    def client_for_run() -> XtreamClient:
        host = select_host(settings.hosts, settings.username, settings.password, settings.state_dir)
        return base_client.with_host(host)

    if args.dump_categories:
        try:
            current = load_categories(settings.categories_path)
        except ConfigError:
            current = Categories(vod=(), series=(), live=())
        try:
            client = client_for_run()
            sys.stdout.write(render(client, current))
        except ProviderError as exc:
            log.error("%s", exc)
            return 1
        return 0
    if args.check_layout:
        try:
            categories = load_categories(settings.categories_path)
            layout = load_layout(settings.layout_path)
            client = client_for_run()
            sys.stdout.write(check_layout(client, categories, layout))
        except ConfigError as exc:
            log.error("%s", exc)
            return 2
        except ProviderError as exc:
            log.error("%s", exc)
            return 1
        return 0
    cache = SeriesCache(settings.state_dir)

    try:
        while True:
            try:
                categories = load_categories(settings.categories_path)
                layout = load_layout(settings.layout_path)
                client = client_for_run()
                summary = run_once(client, settings, categories, cache, layout=layout)
                _log_summary(summary)
            except ConfigError as exc:
                log.error("%s", exc)
                if args.once:
                    return 2
            except (ProviderError, RunAborted, OSError) as exc:
                log.error("run aborted: %s", exc)
                if args.once:
                    return 1
            except Exception:
                log.exception("run failed unexpectedly")
                if args.once:
                    return 1
            if args.once:
                return 0
            log.info("next run in %d s", settings.interval)
            sleep(settings.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
