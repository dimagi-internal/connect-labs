import os
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def pytest_configure(config):
    config.addinivalue_line("markers", "emod: needs Docker and an emodpy-malaria checkout (EMOD_TUTORIALS_DIR)")


def emod_unavailable_reason():
    if not shutil.which("docker"):
        return "docker not installed"
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return "docker daemon not running"
    tutorials = os.environ.get("EMOD_TUTORIALS_DIR", "")
    if not (tutorials and (pathlib.Path(tutorials) / "manifest.py").exists()):
        return "EMOD_TUTORIALS_DIR not set to an emodpy-malaria tutorials dir"
    try:
        import emodpy  # noqa: F401
    except ImportError:
        return "emodpy not installed"
    return None


def pytest_collection_modifyitems(config, items):
    reason = None
    for item in items:
        if "emod" in item.keywords:
            reason = reason or emod_unavailable_reason()
            if reason:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def _activity_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EMOD_ACTIVITY_FILE", str(tmp_path / "run" / "last-activity"))
