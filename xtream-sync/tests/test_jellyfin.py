import requests

from xtream_sync.jellyfin import refresh_guide, refresh_library, scan_running


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


IDLE = [{"Key": "RefreshLibrary", "State": "Idle"}, {"Key": "Other", "State": "Running"}]
RUNNING = [{"Key": "RefreshLibrary", "State": "Running"}]
TASKS_GUIDE_IDLE = [
    {"Key": "RefreshLibrary", "State": "Running", "Id": "lib1"},
    {"Key": "RefreshGuide", "State": "Idle", "Id": "guide1"},
]
TASKS_GUIDE_RUNNING = [{"Key": "RefreshGuide", "State": "Running", "Id": "guide1"}]


class FakeSession:
    def __init__(self, post_response=None, post_exc=None, get_response=None, get_exc=None):
        self.calls = []
        self._post_response = post_response
        self._post_exc = post_exc
        self._get_response = get_response if get_response is not None else FakeResponse(200, IDLE)
        self._get_exc = get_exc

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers, timeout))
        if self._get_exc:
            raise self._get_exc
        return self._get_response

    def post(self, url, headers=None, timeout=None):
        self.calls.append(("POST", url, headers, timeout))
        if self._post_exc:
            raise self._post_exc
        return self._post_response


def test_scan_running_reads_task_state():
    assert scan_running("http://j", "k", session=FakeSession(get_response=FakeResponse(200, RUNNING))) is True
    assert scan_running("http://j", "k", session=FakeSession(get_response=FakeResponse(200, IDLE))) is False


def test_scan_running_unknown_on_failure():
    assert scan_running("http://j", "k", session=FakeSession(get_response=FakeResponse(500))) is None
    assert scan_running("http://j", "k", session=FakeSession(get_exc=requests.ConnectionError())) is None
    assert scan_running("http://j", "k", session=FakeSession(get_response=FakeResponse(200, []))) is None
    assert scan_running("http://j", "k", session=FakeSession(get_response=FakeResponse(200))) is None


def test_refresh_checks_tasks_then_posts_with_token_header():
    session = FakeSession(post_response=FakeResponse(204))
    assert refresh_library("http://jellyfin:8096/", "abc", session=session, timeout=3) is True
    assert [c[0] for c in session.calls] == ["GET", "POST"]
    _method, url, headers, timeout = session.calls[1]
    assert url == "http://jellyfin:8096/Library/Refresh"
    assert headers == {"Authorization": 'MediaBrowser Token="abc"'}
    assert timeout == 3


def test_refresh_does_not_interrupt_a_running_scan():
    session = FakeSession(post_response=FakeResponse(204), get_response=FakeResponse(200, RUNNING))
    assert refresh_library("http://j", "k", session=session) is None
    assert [c[0] for c in session.calls] == ["GET"]


def test_refresh_proceeds_when_scan_state_unknown():
    session = FakeSession(post_response=FakeResponse(204), get_exc=requests.ConnectionError())
    assert refresh_library("http://j", "k", session=session) is True
    assert [c[0] for c in session.calls] == ["GET", "POST"]


def test_refresh_reports_failure_without_raising():
    assert refresh_library("http://j", "k", session=FakeSession(post_response=FakeResponse(401))) is False
    assert refresh_library("http://j", "k", session=FakeSession(post_exc=requests.ConnectionError())) is False


def test_refresh_guide_posts_to_task_id_even_while_library_scan_runs():
    session = FakeSession(post_response=FakeResponse(204), get_response=FakeResponse(200, TASKS_GUIDE_IDLE))
    assert refresh_guide("http://j/", "k", session=session, timeout=4) is True
    method, url, headers, timeout = session.calls[-1]
    assert (method, url) == ("POST", "http://j/ScheduledTasks/Running/guide1")
    assert headers == {"Authorization": 'MediaBrowser Token="k"'}
    assert timeout == 4


def test_refresh_guide_skips_when_running():
    session = FakeSession(post_response=FakeResponse(204), get_response=FakeResponse(200, TASKS_GUIDE_RUNNING))
    assert refresh_guide("http://j", "k", session=session) is None
    assert [c[0] for c in session.calls] == ["GET"]


def test_refresh_guide_task_missing_or_unreadable():
    assert refresh_guide("http://j", "k", session=FakeSession(get_response=FakeResponse(200, IDLE))) is False
    assert refresh_guide("http://j", "k", session=FakeSession(get_exc=requests.ConnectionError())) is None


def test_refresh_guide_post_failure():
    session = FakeSession(post_response=FakeResponse(500), get_response=FakeResponse(200, TASKS_GUIDE_IDLE))
    assert refresh_guide("http://j", "k", session=session) is False
