"""Tests for the web tier's gunicorn worker class (``config/uvicorn_worker.py``).

Covers the env parsing, the "off by default changes nothing" promise, and a drift
guard tying ``docker/start`` to the class it must launch.
"""

from pathlib import Path

import pytest
from uvicorn.workers import UvicornWorker

from config.uvicorn_worker import LIMIT_CONCURRENCY_ENV, LabsUvicornWorker, limit_concurrency_from_env

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
