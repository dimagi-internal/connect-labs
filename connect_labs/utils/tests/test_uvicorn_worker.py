"""Tests for the web tier's gunicorn worker class (``config/uvicorn_worker.py``).

Covers the env parsing, the "off by default changes nothing" promise, and a drift
guard tying ``docker/start`` to the class it must launch.
"""

import inspect
from pathlib import Path

import pytest
from uvicorn.config import Config
from uvicorn.workers import UvicornWorker

from config import uvicorn_worker
from config.uvicorn_worker import (
    LIMIT_CONCURRENCY_ENV,
    RECYCLE_RSS_ENV,
    LabsUvicornWorker,
    RecyclingServer,
    limit_concurrency_from_env,
    recycle_rss_kb_from_env,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestLimitConcurrencyFromEnv:
    @pytest.mark.parametrize("environ", [{}, {LIMIT_CONCURRENCY_ENV: ""}, {LIMIT_CONCURRENCY_ENV: "   "}])
    def test_absent_or_blank_disables_the_valve(self, environ):
        assert limit_concurrency_from_env(environ) is None

    def test_zero_is_an_explicit_off(self):
        """0 has to mean off, so the var can stay in web.json documenting the knob."""
        assert limit_concurrency_from_env({LIMIT_CONCURRENCY_ENV: "0"}) is None

    @pytest.mark.parametrize(("raw", "expected"), [("1", 1), ("50", 50), (" 100 ", 100)])
    def test_positive_values_pass_through(self, raw, expected):
        assert limit_concurrency_from_env({LIMIT_CONCURRENCY_ENV: raw}) == expected

    @pytest.mark.parametrize("raw", ["abc", "50x", "1.5", "1e3"])
    def test_malformed_raises_rather_than_silently_disabling(self, raw):
        """A typo must not leave the operator believing the valve is armed."""
        with pytest.raises(ValueError, match=LIMIT_CONCURRENCY_ENV):
            limit_concurrency_from_env({LIMIT_CONCURRENCY_ENV: raw})

    @pytest.mark.parametrize("raw", ["-1", "-100"])
    def test_negative_raises(self, raw):
        with pytest.raises(ValueError, match=LIMIT_CONCURRENCY_ENV):
            limit_concurrency_from_env({LIMIT_CONCURRENCY_ENV: raw})


class TestLabsUvicornWorker:
    def test_preserves_the_base_worker_config(self):
        """Adding our key must not clobber uvicorn's own CONFIG_KWARGS (loop/http)."""
        for key, value in UvicornWorker.CONFIG_KWARGS.items():
            assert LabsUvicornWorker.CONFIG_KWARGS[key] == value

    def test_declares_limit_concurrency(self):
        assert "limit_concurrency" in LabsUvicornWorker.CONFIG_KWARGS

    def test_defaults_to_unbounded_in_a_clean_environment(self, monkeypatch):
        """Off by default: with the var unset the config matches plain UvicornWorker."""
        monkeypatch.delenv(LIMIT_CONCURRENCY_ENV, raising=False)
        assert limit_concurrency_from_env() is None

    def test_is_a_uvicorn_worker(self):
        assert issubclass(LabsUvicornWorker, UvicornWorker)


class TestStartScriptWiring:
    """The registered container command has silently diverged from the repo before.

    ``deploy/task-definitions/README.md`` records the incident: ``docker/start``
    moved to ASGI while the registered web command stayed on the old WSGI
    gunicorn, and ``/mcp/`` 404'd in production until it was fixed by hand. These
    pin the same class of divergence for the worker class.
    """

    @property
    def start_script(self) -> str:
        return (REPO_ROOT / "docker" / "start").read_text()

    def test_start_launches_the_labs_worker_class(self):
        assert "-k config.uvicorn_worker.LabsUvicornWorker" in self.start_script

    def test_keep_alive_outlasts_the_alb_idle_timeout(self):
        """The ALB's idle timeout is 600s; a target that closes an idle pooled
        connection first races the ALB reusing it, and that request is a 502
        with target_status "-". The target must hold longer than the ALB.

        Also pins that gunicorn's keepalive is what reaches uvicorn: a uvicorn
        upgrade that stopped mapping it would make the flag silently inert.
        """
        import re

        match = re.search(r"--keep-alive\s+(\d+)", self.start_script)
        assert match, "docker/start must set --keep-alive above the ALB idle timeout (600s)"
        assert int(match.group(1)) > 600
        assert '"timeout_keep_alive": self.cfg.keepalive' in inspect.getsource(UvicornWorker.__init__)

    def test_start_no_longer_launches_uvicorns_worker_directly(self):
        """Bypassing our subclass would silently drop the valve entirely."""
        assert "-k uvicorn.workers.UvicornWorker" not in self.start_script

    def test_web_task_definition_carries_the_knob_disabled(self):
        """Ships off; the var stays present so enabling it is a one-value diff."""
        import json

        task_def = json.loads((REPO_ROOT / "deploy" / "task-definitions" / "web.json").read_text())
        env = {
            item["name"]: item["value"]
            for container in task_def["containerDefinitions"]
            for item in container.get("environment", [])
        }
        assert env[LIMIT_CONCURRENCY_ENV] == "0"


class TestRecycleRssFromEnv:
    @pytest.mark.parametrize("environ", [{}, {RECYCLE_RSS_ENV: ""}, {RECYCLE_RSS_ENV: "0"}])
    def test_absent_blank_or_zero_is_off(self, environ):
        assert recycle_rss_kb_from_env(environ) is None

    def test_megabytes_become_kilobytes(self):
        assert recycle_rss_kb_from_env({RECYCLE_RSS_ENV: "1024"}) == 1024 * 1024

    @pytest.mark.parametrize("raw", ["abc", "1.5", "-1"])
    def test_bad_values_raise(self, raw):
        with pytest.raises(ValueError, match=RECYCLE_RSS_ENV):
            recycle_rss_kb_from_env({RECYCLE_RSS_ENV: raw})


class TestRecyclingServer:
    def _server(self, tmp_path, limit_kb=1000):
        return RecyclingServer(Config(app=lambda *a: None), rss_limit_kb=limit_kb, lock_path=str(tmp_path / "lock"))

    def test_under_the_ceiling_keeps_serving(self, tmp_path, monkeypatch):
        monkeypatch.setattr(uvicorn_worker, "current_rss_kb", lambda: 999)
        server = self._server(tmp_path)
        assert server.maybe_recycle() is False
        assert server.should_exit is False

    def test_over_the_ceiling_starts_a_graceful_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(uvicorn_worker, "current_rss_kb", lambda: 1000)
        server = self._server(tmp_path)
        assert server.maybe_recycle() is True
        assert server.should_exit is True
        assert server.force_exit is False  # graceful: in-flight requests finish

    def test_only_one_worker_per_task_drains_at_a_time(self, tmp_path, monkeypatch):
        """All three draining together would leave the task accepting nothing."""
        monkeypatch.setattr(uvicorn_worker, "current_rss_kb", lambda: 5000)
        first, second = self._server(tmp_path), self._server(tmp_path)
        assert first.maybe_recycle() is True
        assert second.maybe_recycle() is False
        first._recycle_lock.close()  # what process exit does
        assert second.maybe_recycle() is True

    def test_unreadable_rss_never_recycles(self, tmp_path, monkeypatch):
        monkeypatch.setattr(uvicorn_worker, "current_rss_kb", lambda: None)
        assert self._server(tmp_path).maybe_recycle() is False

    def test_parent_serve_is_what_ours_mirrors(self):
        """Our _serve copies UvicornWorker._serve with the server class swapped; if a
        uvicorn upgrade changes the parent, re-read it and update ours."""
        src = inspect.getsource(UvicornWorker._serve)
        for line in (
            "self.config.app = self.wsgi",
            "server = Server(config=self.config)",
            "self._install_sigquit_handler()",
            "await server.serve(sockets=self.sockets)",
            "sys.exit(Arbiter.WORKER_BOOT_ERROR)",
        ):
            assert line in src

    def test_web_task_definition_enables_recycling(self):
        import json

        task_def = json.loads((REPO_ROOT / "deploy" / "task-definitions" / "web.json").read_text())
        env = {
            item["name"]: item["value"]
            for container in task_def["containerDefinitions"]
            for item in container.get("environment", [])
        }
        assert recycle_rss_kb_from_env(env) == 1024 * 1024
