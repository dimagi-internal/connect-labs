"""Run v4's job handler against the parity fixture and dump JSON.

Usage:
    python -m commcare_connect.workflow.tests.mbw_v4_v5_parity.run_v4 [tab2]

Without args → Tab 1 (no task_filters). With `tab2` → Tab 2 (with task_filters).
Output is sorted JSON on stdout so it's stable for byte-level diff.
"""

import json
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from commcare_connect.workflow.job_handlers.mbw_auditing_v4 import (  # noqa: E402
    handle_mbw_auditing_v4_job,
)
from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import (  # noqa: E402
    build_fixture,
    fixture_for_tab2,
)


def main() -> None:
    tab2 = len(sys.argv) > 1 and sys.argv[1] == "tab2"
    fixture = fixture_for_tab2() if tab2 else build_fixture()

    job_config = {
        "pipeline_data": {
            "visits": {"rows": fixture["visits"]},
            "visits_agg": {"rows": fixture["visits_agg"]},
            "registrations": {"rows": fixture["registrations"]},
            "gs_forms": {"rows": fixture["gs_forms"]},
        },
        "active_usernames": fixture["active_usernames"],
        "flw_names": fixture["flw_names"],
        "current_date": fixture["current_date"],
        # opportunity_id + access_token deliberately omitted so the handler
        # skips the open_tasks / prev_categories external lookups (they're not
        # part of the SQL-compute parity surface — v5 calls REST endpoints
        # separately).
    }
    if tab2:
        job_config["task_filters"] = fixture["task_filters"]

    def _progress(msg, **_):
        pass

    result = handle_mbw_auditing_v4_job(job_config, access_token="", progress_callback=_progress)
    # Strip keys that aren't part of the per-FLW parity surface so the diff
    # is signal-only.
    result.pop("open_tasks", None)
    result.pop("open_tasks_debug", None)
    result.pop("prev_categories", None)
    print(json.dumps(result, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
