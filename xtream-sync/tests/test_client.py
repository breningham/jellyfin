from urllib.parse import quote_plus

import pytest
import requests

from xtream_sync.client import Credentials, ProviderError, XtreamClient, scrub_secrets

CREDS = Credentials("http://host.example/", "user", "pass")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, bad_json=False, text="", content=None):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json
        self.text = text
        self.content = content if content is not None else text.encode()

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.headers = {}
        self.calls = []
        self._response = response
        self._exc = exc

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        if self._exc:
            raise self._exc
        return self._response


def test_credentials_strip_trailing_slash():
    assert CREDS.host == "http://host.example"


def test_vod_streams_builds_query_and_returns_list():
    session = FakeSession(FakeResponse(payload=[{"stream_id": 1}]))
    client = XtreamClient(CREDS, session=session, timeout=5)
    assert client.vod_streams(119) == [{"stream_id": 1}]
    url, params, timeout = session.calls[0]
    assert url == "http://host.example/player_api.php"
    assert params == {
        "username": "user",
        "password": "pass",
        "action": "get_vod_streams",
        "category_id": 119,
    }
    assert timeout == 5
    assert session.headers["User-Agent"].startswith("xtream-sync/")


def test_empty_dict_is_treated_as_empty_list():
    session = FakeSession(FakeResponse(payload={}))
    assert XtreamClient(CREDS, session=session).series(1) == []


def test_series_info_returns_dict():
    session = FakeSession(FakeResponse(payload={"episodes": {}}))
    assert XtreamClient(CREDS, session=session).series_info(7) == {"episodes": {}}
    assert session.calls[0][1]["action"] == "get_series_info"
    assert session.calls[0][1]["series_id"] == 7


@pytest.mark.parametrize(
    "session",
    [
        FakeSession(FakeResponse(status_code=404, payload=[])),
        FakeSession(FakeResponse(bad_json=True)),
        FakeSession(exc=requests.ConnectionError("boom")),
        FakeSession(FakeResponse(payload="unexpected")),
    ],
)
def test_failures_raise_provider_error(session):
    with pytest.raises(ProviderError):
        XtreamClient(CREDS, session=session).vod_categories()


def test_series_info_non_dict_raises():
    session = FakeSession(FakeResponse(payload=[]))
    with pytest.raises(ProviderError):
        XtreamClient(CREDS, session=session).series_info(1)


def test_connection_error_scrubs_credentials():
    session = FakeSession(
        exc=requests.ConnectionError(
            "Max retries exceeded with url: /player_api.php?username=user&password=pass&action=x"
        )
    )
    client = XtreamClient(CREDS, session=session)
    with pytest.raises(ProviderError) as excinfo:
        client.vod_categories()
    message = str(excinfo.value)
    assert "username=user" not in message
    assert "password=pass" not in message
    assert "***" in message


def test_connection_error_scrubs_quote_plus_encoded_credentials():
    password = "p@ss/w+d"
    quoted = quote_plus(password)
    creds = Credentials("http://host.example/", "user", password)
    session = FakeSession(
        exc=requests.ConnectionError(
            f"Max retries exceeded with url: /player_api.php?username=user&password={quoted}&action=x"
        )
    )
    client = XtreamClient(creds, session=session)
    with pytest.raises(ProviderError) as excinfo:
        client.vod_categories()
    message = str(excinfo.value)
    assert quoted not in message
    assert password not in message
    assert "***" in message


def test_stream_urls():
    client = XtreamClient(CREDS, session=FakeSession())
    assert client.movie_url(2145896, "mkv") == "http://host.example/movie/user/pass/2145896.mkv"
    assert client.episode_url(2141519, "mp4") == "http://host.example/series/user/pass/2141519.mp4"


def test_stream_urls_percent_encode_special_characters_in_credentials():
    creds = Credentials("http://host.example/", "user", "p@ss/w")
    client = XtreamClient(creds, session=FakeSession())
    assert client.movie_url(1, "mkv") == "http://host.example/movie/user/p%40ss%2Fw/1.mkv"
    assert client.episode_url(1, "mp4") == "http://host.example/series/user/p%40ss%2Fw/1.mp4"


def test_scrub_secrets_masks_raw_and_encoded():
    text = "url ?username=us%40er&password=p%40ss and raw us@er p@ss"
    assert scrub_secrets(text, "us@er", "p@ss") == "url ?username=***&password=*** and raw *** ***"
    assert scrub_secrets("nothing", "") == "nothing"


def test_scrub_secrets_matches_whole_tokens_only():
    assert scrub_secrets("auth rejected", "u") == "auth rejected"
    assert scrub_secrets("username=u&password=p", "u", "p") == "username=***&password=***"
    assert scrub_secrets("/movie/user/pass/12.mkv", "user", "pass") == "/movie/***/***/12.mkv"
    assert scrub_secrets("username=user&x", "user") == "username=***&x"
    assert scrub_secrets("bogus_user here", "user") == "bogus_*** here"


def test_with_host_switches_host_but_keeps_session_and_creds():
    session = FakeSession(FakeResponse(payload=[]))
    a = XtreamClient(CREDS, session=session)
    b = a.with_host("http://mirror.example/")
    assert b.host == "http://mirror.example"
    assert a.host == "http://host.example"
    b.vod_categories()
    assert session.calls[0][0] == "http://mirror.example/player_api.php"
    assert session.calls[0][1]["username"] == "user"


def test_live_endpoints():
    session = FakeSession(FakeResponse(payload=[{"stream_id": 5}]))
    client = XtreamClient(CREDS, session=session)
    assert client.live_categories() == [{"stream_id": 5}]
    assert session.calls[-1][1]["action"] == "get_live_categories"
    assert client.live_streams(1141) == [{"stream_id": 5}]
    assert session.calls[-1][1]["action"] == "get_live_streams"
    assert session.calls[-1][1]["category_id"] == 1141
    assert client.live_url(639184) == "http://host.example/live/user/pass/639184.ts"


def test_xmltv_returns_text():
    session = FakeSession(FakeResponse(text="<tv></tv>"))
    client = XtreamClient(CREDS, session=session)
    assert client.xmltv() == b"<tv></tv>"
    url, params, _timeout = session.calls[0]
    assert url == "http://host.example/xmltv.php"
    assert params == {"username": "user", "password": "pass"}


@pytest.mark.parametrize(
    "session",
    [
        FakeSession(FakeResponse(status_code=500, text="")),
        FakeSession(FakeResponse(text="")),
        FakeSession(exc=requests.ConnectionError("password=pass")),
    ],
)
def test_xmltv_failures_raise_scrubbed_provider_error(session):
    with pytest.raises(ProviderError) as excinfo:
        XtreamClient(CREDS, session=session).xmltv()
    assert "password=pass" not in str(excinfo.value)
