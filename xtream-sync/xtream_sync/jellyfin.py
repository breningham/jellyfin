"""Ask Jellyfin to rescan its libraries."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

SCAN_TASK_KEY = "RefreshLibrary"
GUIDE_TASK_KEY = "RefreshGuide"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f'MediaBrowser Token="{api_key}"'}


def _tasks(
    base_url: str, api_key: str, session: requests.Session, timeout: float
) -> list[dict] | None:
    url = f"{base_url.rstrip('/')}/ScheduledTasks"
    try:
        resp = session.get(url, headers=_headers(api_key), timeout=timeout)
        if resp.status_code >= 300:
            log.warning("jellyfin task query failed: HTTP %s", resp.status_code)
            return None
        tasks = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("jellyfin task query failed: %s", exc)
        return None
    return tasks if isinstance(tasks, list) else None


def _find_task(tasks: list[dict] | None, key: str) -> dict | None:
    for task in tasks or []:
        if isinstance(task, dict) and task.get("Key") == key:
            return task
    return None


def scan_running(
    base_url: str,
    api_key: str,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> bool | None:
    """True/False if Jellyfin says its library scan is running/idle; None if unknown."""
    session = session or requests.Session()
    task = _find_task(_tasks(base_url, api_key, session, timeout), SCAN_TASK_KEY)
    if task is None:
        return None
    return task.get("State") != "Idle"


def refresh_library(
    base_url: str,
    api_key: str,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> bool | None:
    """Request a library scan. Never interrupts a scan already in progress.

    Jellyfin's /Library/Refresh cancels a running scan and starts over, so if
    one is running we leave it alone and return None; the next sync run will
    try again. True means accepted, False means the request failed.
    """
    session = session or requests.Session()
    if scan_running(base_url, api_key, session=session, timeout=timeout):
        log.info("jellyfin library scan already running; not requesting another")
        return None
    url = f"{base_url.rstrip('/')}/Library/Refresh"
    try:
        resp = session.post(url, headers=_headers(api_key), timeout=timeout)
    except requests.RequestException as exc:
        log.warning("jellyfin refresh failed: %s", exc)
        return False
    if resp.status_code >= 300:
        log.warning("jellyfin refresh failed: HTTP %s", resp.status_code)
        return False
    log.info("jellyfin library refresh requested")
    return True


def refresh_guide(
    base_url: str,
    api_key: str,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> bool | None:
    """Run Jellyfin's 'Refresh Guide' task unless it is already running.

    Returns None when skipped (running, or task list unreadable), False when
    the task is missing or the request failed, True when accepted.
    """
    session = session or requests.Session()
    tasks = _tasks(base_url, api_key, session, timeout)
    if tasks is None:
        return None
    task = _find_task(tasks, GUIDE_TASK_KEY)
    if task is None or not task.get("Id"):
        log.warning("jellyfin guide refresh task not found")
        return False
    if task.get("State") != "Idle":
        log.info("jellyfin guide refresh already running; not requesting another")
        return None
    url = f"{base_url.rstrip('/')}/ScheduledTasks/Running/{task['Id']}"
    try:
        resp = session.post(url, headers=_headers(api_key), timeout=timeout)
    except requests.RequestException as exc:
        log.warning("jellyfin guide refresh failed: %s", exc)
        return False
    if resp.status_code >= 300:
        log.warning("jellyfin guide refresh failed: HTTP %s", resp.status_code)
        return False
    log.info("jellyfin guide refresh requested")
    return True
