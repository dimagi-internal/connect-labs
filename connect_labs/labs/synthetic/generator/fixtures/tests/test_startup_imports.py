"""Starting Django must not import scipy.stats.

This package is on every process's startup path (the MCP tools import the
synthetic generator), and scipy.stats alone was ~0.9s of each web, celery and
test worker's boot. The modules that need it import it where they use it. A
top-level `from scipy.stats import ...` anywhere on that path brings the cost
back without failing anything else, so this checks it in a fresh interpreter.
"""

import os
import subprocess
import sys


def test_django_setup_does_not_import_scipy_stats():
    code = "import sys, django; django.setup(); print('scipy.stats' in sys.modules)"
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "False", "something on Django's startup path imports scipy.stats"
