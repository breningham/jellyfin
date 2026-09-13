"""Thin client for the Xtream Codes `player_api.php` API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, quote_plus

import requests

USER_AGENT = "xtream-sync/0.1"


def scrub_secrets(text: str, *secrets: str) -> str:
    """Replace each secret (raw and quote_plus-encoded) with '***'.

    A secret is only masked as a whole token - not glued to other ASCII
    letters or digits - so a short secret cannot mangle unrelated words.
    Credentials in URLs and messages are always delimited by =, &, / or
    whitespace, so this loses nothing in practice.
    """
    needles = {form for raw in secrets if raw for form in (raw, quote_plus(raw))}
    for needle in sorted(needles, key=len, reverse=True):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
        text = re.sub(pattern, "***", text)
    return text


class ProviderError(Exception):
    """The provider returned an error, bad data, or was unreachable."""


@dataclass(frozen=True)
class Credentials:
    host: str
    username: str
    password: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", self.host.rstrip("/"))


class XtreamClient:
    def __init__(
        self,
        creds: Credentials,
        session: requests.Session | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._creds = creds
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._timeout = timeout

    @property
    def host(self) -> str:
        return self._creds.host

    def with_host(self, host: str) -> XtreamClient:
        """Same credentials and session, different base URL."""
        creds = Credentials(host, self._creds.username, self._creds.password)
        return XtreamClient(creds, session=self._session, timeout=self._timeout)

    # -- raw calls -----------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """Replace credentials (raw and quote_plus-encoded) with '***'."""
        return scrub_secrets(text, self._creds.username, self._creds.password)

    def _get(self, action: str, **params: Any) -> Any:
        query = {
            "username": self._creds.username,
            "password": self._creds.password,
            "action": action,
            **params,
        }
        try:
            resp = self._session.get(
                f"{self._creds.host}/player_api.php",
                params=query,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(self._scrub(f"{action}: {exc}")) from exc
        if resp.status_code != 200:
            raise ProviderError(self._scrub(f"{action}: HTTP {resp.status_code}"))
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(self._scrub(f"{action}: response is not JSON")) from exc

    def _get_list(self, action: str, **params: Any) -> list[dict]:
        data = self._get(action, **params)
        if isinstance(data, dict) and not data:
            return []  # some panels return {} for an empty category
        if not isinstance(data, list):
            raise ProviderError(
                self._scrub(f"{action}: expected a list, got {type(data).__name__}")
            )
        return data

    # -- catalogue -----------------------------------------------------------

    def vod_categories(self) -> list[dict]:
        return self._get_list("get_vod_categories")

    def series_categories(self) -> list[dict]:
        return self._get_list("get_series_categories")

    def vod_streams(self, category_id: int) -> list[dict]:
        return self._get_list("get_vod_streams", category_id=category_id)

    def series(self, category_id: int) -> list[dict]:
        return self._get_list("get_series", category_id=category_id)

    def series_info(self, series_id: int) -> dict:
        data = self._get("get_series_info", series_id=series_id)
        if not isinstance(data, dict):
            raise ProviderError(
                self._scrub(f"get_series_info: expected an object for series {series_id}")
            )
        return data

    def live_categories(self) -> list[dict]:
        return self._get_list("get_live_categories")

    def live_streams(self, category_id: int) -> list[dict]:
        return self._get_list("get_live_streams", category_id=category_id)

    def xmltv(self) -> bytes:
        """Fetch the provider's full XMLTV guide as bytes.

        Returned as raw bytes (not decoded text) so the caller can hand
        them to `ET.iterparse` and let expat honour the document's own
        declared encoding instead of guessing.
        """
        query = {"username": self._creds.username, "password": self._creds.password}
        try:
            resp = self._session.get(
                f"{self._creds.host}/xmltv.php", params=query, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise ProviderError(self._scrub(f"xmltv: {exc}")) from exc
        if resp.status_code != 200:
            raise ProviderError(self._scrub(f"xmltv: HTTP {resp.status_code}"))
        if not resp.content:
            raise ProviderError("xmltv: empty response")
        return resp.content

    # -- stream URLs ---------------------------------------------------------

    def movie_url(self, stream_id: int, ext: str) -> str:
        c = self._creds
        user = quote(c.username, safe="")
        password = quote(c.password, safe="")
        return f"{c.host}/movie/{user}/{password}/{stream_id}.{ext}"

    def episode_url(self, episode_id: int, ext: str) -> str:
        c = self._creds
        user = quote(c.username, safe="")
        password = quote(c.password, safe="")
        return f"{c.host}/series/{user}/{password}/{episode_id}.{ext}"

    def live_url(self, stream_id: int) -> str:
        c = self._creds
        user = quote(c.username, safe="")
        password = quote(c.password, safe="")
        return f"{c.host}/live/{user}/{password}/{stream_id}.ts"
