import json

import pytest
import requests

from xtream_sync.client import ProviderError
from xtream_sync.hosts import (
    Probe,
    choose_host,
    load_previous,
    probe_host,
    save_choice,
    select_host,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Routes GETs by host prefix; each entry is a response or an exception."""

    def __init__(self, routes):
        self.headers = {}
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        for prefix, result in self.routes.items():
            if url.startswith(prefix):
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unexpected url {url}")


AUTH_OK = FakeResponse(payload={"user_info": {"auth": 1}})


def test_probe_ok_and_timing():
    session = FakeSession({"http://a": AUTH_OK})
    p = probe_host("http://a", "someuser", "secretpw", session, timeout=5)
    assert p.ok is True and p.host == "http://a" and p.elapsed_ms >= 0
    url, params, timeout = session.calls[0]
    assert url == "http://a/player_api.php"
    assert params == {"username": "someuser", "password": "secretpw"}
    # split (connect, read) timeout: default 3s connect, the caller's read timeout
    assert timeout == (3.0, 5)


@pytest.mark.parametrize(
    "result, snippet",
    [
        (FakeResponse(status_code=403), "HTTP 403"),
        (FakeResponse(payload={"user_info": {"auth": 0}}), "auth"),
        (FakeResponse(bad_json=True), "JSON"),
        (requests.ConnectionError("password=secretpw boom"), "boom"),
    ],
)
def test_probe_failures_are_not_ok_and_scrubbed(result, snippet):
    p = probe_host("http://a", "someuser", "secretpw", FakeSession({"http://a": result}))
    assert p.ok is False
    assert snippet in p.detail
    assert "secretpw" not in p.detail and "someuser" not in p.detail


def test_choose_first_run_picks_fastest():
    probes = [Probe("a", True, 120, "ok"), Probe("b", True, 95, "ok")]
    assert choose_host(probes, previous=None) == "b"


def test_choose_keeps_previous_within_margin():
    probes = [Probe("a", True, 120, "ok"), Probe("b", True, 95, "ok")]
    assert choose_host(probes, previous="a") == "a"


def test_choose_switches_when_clearly_faster():
    probes = [Probe("a", True, 200, "ok"), Probe("b", True, 100, "ok")]
    assert choose_host(probes, previous="a") == "b"
    probes = [Probe("a", True, 100, "ok"), Probe("b", True, 70, "ok")]
    assert choose_host(probes, previous="a") == "b"  # exactly 30% faster switches


def test_choose_switches_when_previous_unhealthy():
    probes = [Probe("a", False, 10000, "HTTP 502"), Probe("b", True, 300, "ok")]
    assert choose_host(probes, previous="a") == "b"


def test_choose_ignores_unknown_previous():
    probes = [Probe("a", True, 50, "ok")]
    assert choose_host(probes, previous="gone") == "a"


def test_choose_none_healthy_raises_with_details():
    probes = [Probe("a", False, 0, "HTTP 502"), Probe("b", False, 0, "timeout")]
    with pytest.raises(ProviderError, match="502"):
        choose_host(probes, previous=None)


def test_previous_choice_round_trip(tmp_path):
    assert load_previous(tmp_path) is None
    save_choice(tmp_path, "http://a")
    assert load_previous(tmp_path) == "http://a"
    assert json.loads((tmp_path / "host.json").read_text()) == {"host": "http://a"}
    (tmp_path / "host.json").write_text("{bad")
    assert load_previous(tmp_path) is None


def test_select_host_probes_all_persists_and_logs(tmp_path, caplog):
    session = FakeSession({"http://a": AUTH_OK, "http://b": requests.ConnectionError("down")})
    with caplog.at_level("INFO"):
        chosen = select_host(("http://a", "http://b"), "someuser", "secretpw", tmp_path, session=session)
    assert chosen == "http://a"
    assert load_previous(tmp_path) == "http://a"
    assert len(session.calls) == 2
    assert "host check:" in caplog.text and "using http://a" in caplog.text


def test_select_host_single_host_still_probed(tmp_path):
    session = FakeSession({"http://a": FakeResponse(status_code=500)})
    with pytest.raises(ProviderError):
        select_host(("http://a",), "someuser", "secretpw", tmp_path, session=session)


def test_select_host_log_truncates_long_probe_detail(tmp_path, caplog):
    long_detail = "x" * 200
    session = FakeSession(
        {"http://a": requests.ConnectionError(long_detail), "http://b": AUTH_OK}
    )
    with caplog.at_level("INFO"):
        select_host(("http://a", "http://b"), "someuser", "secretpw", tmp_path, session=session)
    assert long_detail not in caplog.text
    assert ("x" * 80 + "…") in caplog.text


def test_probe_scrubs_url_encoded_credentials():
    """Test that URL-encoded forms of credentials (e.g., @ -> %40) are also scrubbed."""
    session = FakeSession({"http://a": requests.ConnectionError("username=some%40user boom")})
    p = probe_host("http://a", "some@user", "pass", session)
    assert p.ok is False
    assert "boom" in p.detail
    assert "some@user" not in p.detail
    assert "some%40user" not in p.detail
