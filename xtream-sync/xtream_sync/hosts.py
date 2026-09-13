"""Pick the healthiest provider host from XTREAM_HOST, with hysteresis."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .client import USER_AGENT, ProviderError, scrub_secrets

log = logging.getLogger(__name__)

CHOICE_FILE = "host.json"
DEFAULT_MARGIN = 0.3
DETAIL_LOG_LIMIT = 80


def _truncate(text: str, limit: int = DETAIL_LOG_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass(frozen=True)
class Probe:
    host: str
    ok: bool
    elapsed_ms: float
    detail: str


def probe_host(
    host: str,
    username: str,
    password: str,
    session: requests.Session,
    timeout: float = 10.0,
    connect_timeout: float = 3.0,
) -> Probe:
    """One authenticated player_api call; ok when HTTP 200, JSON, and auth == 1."""
    started = time.perf_counter()

    def finish(ok: bool, detail: str) -> Probe:
        elapsed = (time.perf_counter() - started) * 1000
        return Probe(host, ok, elapsed, scrub_secrets(detail, username, password))

    try:
        resp = session.get(
            f"{host}/player_api.php",
            params={"username": username, "password": password},
            timeout=(connect_timeout, timeout),
        )
    except requests.RequestException as exc:
        return finish(False, str(exc))
    if resp.status_code != 200:
        return finish(False, f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return finish(False, "response is not JSON")
    auth = (data.get("user_info") or {}).get("auth") if isinstance(data, dict) else None
    if str(auth) != "1":
        return finish(False, f"auth rejected (auth={auth!r})")
    return finish(True, "ok")


def choose_host(probes: list[Probe], previous: str | None, margin: float = DEFAULT_MARGIN) -> str:
    """Fastest healthy host, unless the previous one is healthy and within `margin`."""
    healthy = [p for p in probes if p.ok]
    if not healthy:
        details = "; ".join(f"{p.host}: {p.detail}" for p in probes)
        raise ProviderError(f"no healthy provider host: {details}")
    fastest = min(healthy, key=lambda p: p.elapsed_ms)
    prev = next((p for p in healthy if p.host == previous), None)
    if prev is not None and fastest.elapsed_ms > prev.elapsed_ms * (1 - margin):
        return prev.host
    return fastest.host


def load_previous(state_dir: Path) -> str | None:
    path = state_dir / CHOICE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    host = data.get("host") if isinstance(data, dict) else None
    return host if isinstance(host, str) and host else None


def save_choice(state_dir: Path, host: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (CHOICE_FILE + ".tmp")
    tmp.write_text(json.dumps({"host": host}), encoding="utf-8")
    os.replace(tmp, state_dir / CHOICE_FILE)


def select_host(
    hosts: tuple[str, ...],
    username: str,
    password: str,
    state_dir: Path,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> str:
    """Probe every host, choose with hysteresis, persist and log the choice."""
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    probes = [probe_host(h, username, password, session, timeout=timeout) for h in hosts]
    previous = load_previous(state_dir)
    chosen = choose_host(probes, previous)
    save_choice(state_dir, chosen)
    report = ", ".join(
        f"{p.host} {p.elapsed_ms:.0f}ms {'ok' if p.ok else 'FAIL ' + _truncate(p.detail)}"
        for p in probes
    )
    verdict = "kept" if chosen == previous else ("first run" if previous is None else "switched")
    log.info("host check: %s -> using %s (%s)", report, chosen, verdict)
    return chosen
