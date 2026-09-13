import pytest

from xtream_sync import __main__ as cli
from xtream_sync.client import ProviderError
from xtream_sync.sync import RunAborted, RunSummary
from xtream_sync.writer import Diff


def _summary():
    return RunSummary(movies=1, shows=0, episodes=0, diff=Diff(), series_fetched=0, series_cached=0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    cats = tmp_path / "c.yaml"
    cats.write_text("vod: [1]\n")
    for key, value in {
        "XTREAM_HOST": "http://h",
        "XTREAM_USERNAME": "u",
        "XTREAM_PASSWORD": "p",
        "XTREAM_OUTPUT_DIR": str(tmp_path / "out"),
        "XTREAM_STATE_DIR": str(tmp_path / "state"),
        "XTREAM_CATEGORIES": str(cats),
        "XTREAM_LAYOUT": str(tmp_path / "layout.yaml"),
        "XTREAM_SYNC_INTERVAL": "7",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    monkeypatch.setattr(cli, "select_host", lambda hosts, *a, **k: hosts[0])
    return tmp_path


def test_missing_env_exits_2(monkeypatch):
    for key in ("XTREAM_HOST", "XTREAM_USERNAME", "XTREAM_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["--once"]) == 2


def test_bad_categories_exits_2(env, monkeypatch):
    monkeypatch.setenv("XTREAM_CATEGORIES", str(env / "missing.yaml"))
    assert cli.main(["--once"]) == 2


def test_bad_layout_exits_2_once(env, monkeypatch):
    (env / "layout.yaml").write_text("rules: 3\n")
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: _summary())
    assert cli.main(["--once"]) == 2


def test_layout_is_passed_to_run_once(env, monkeypatch):
    (env / "layout.yaml").write_text('rules:\n  - match: {}\n    into: X\n')
    seen = {}
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: seen.update(k) or _summary())
    assert cli.main(["--once"]) == 0
    assert len(seen["layout"].rules) == 1


def test_once_runs_a_single_pass(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: calls.append(a) or _summary())
    assert cli.main(["--once"]) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("exc", [ProviderError("down"), RunAborted("empty"), OSError("disk")])
def test_once_failure_exits_1(env, monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(cli, "run_once", boom)
    assert cli.main(["--once"]) == 1


def test_once_unexpected_exception_exits_1(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "run_once", boom)
    assert cli.main(["--once"]) == 1


def test_loop_unexpected_exception_falls_through_to_sleep(env, monkeypatch):
    outcomes = [RuntimeError("kaboom"), _summary()]
    sleeps = []

    def run(*a, **k):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_once", run)
    monkeypatch.setattr(cli, "sleep", sleep)
    assert cli.main([]) == 0
    assert sleeps == [7, 7]


def test_loop_sleeps_interval_and_survives_failures(env, monkeypatch):
    outcomes = [ProviderError("down"), _summary()]
    sleeps = []

    def run(*a, **k):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_once", run)
    monkeypatch.setattr(cli, "sleep", sleep)
    assert cli.main([]) == 0
    assert sleeps == [7, 7]


def test_dump_categories_prints_template_and_exits_0(env, monkeypatch, capsys):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def vod_categories(self):
            return [{"category_id": "1", "category_name": "A"}, {"category_id": "2", "category_name": "B"}]

        def series_categories(self):
            return []

        def live_categories(self):
            return []

        def with_host(self, host):
            return self

    monkeypatch.setattr(cli, "XtreamClient", FakeClient)
    assert cli.main(["--dump-categories"]) == 0
    out = capsys.readouterr().out
    assert "  - 1     # A" in out
    assert "  #- 2     # B" in out
    assert "series:" in out


def test_dump_categories_works_with_no_current_selection(env, monkeypatch, capsys):
    (env / "c.yaml").write_text("vod: []\n")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def vod_categories(self):
            return [{"category_id": "1", "category_name": "A"}]

        def series_categories(self):
            return []

        def live_categories(self):
            return []

        def with_host(self, host):
            return self

    monkeypatch.setattr(cli, "XtreamClient", FakeClient)
    assert cli.main(["--dump-categories"]) == 0
    assert "  #- 1     # A" in capsys.readouterr().out


def test_host_selected_before_each_run(env, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "select_host", lambda hosts, u, p, state_dir, **k: seen.append(hosts) or "http://picked")
    hosts_used = []

    def run(client, *a, **k):
        hosts_used.append(client.host)
        return _summary()

    monkeypatch.setattr(cli, "run_once", run)
    monkeypatch.setenv("XTREAM_HOST", "http://h1, http://h2")
    assert cli.main(["--once"]) == 0
    assert seen == [("http://h1", "http://h2")]
    assert hosts_used == ["http://picked"]


def test_no_healthy_host_exits_1_in_once_mode(env, monkeypatch):
    def boom(*a, **k):
        raise ProviderError("no healthy provider host")

    monkeypatch.setattr(cli, "select_host", boom)
    assert cli.main(["--once"]) == 1


def test_check_layout_prints_report_and_exits_0(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "check_layout", lambda *a, **k: "report\n")
    assert cli.main(["--check-layout"]) == 0
    assert capsys.readouterr().out == "report\n"
