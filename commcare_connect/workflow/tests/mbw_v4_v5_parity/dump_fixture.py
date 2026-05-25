"""Dump the parity fixture as JSON so the node harness can consume it.

Usage:
    python -m commcare_connect.workflow.tests.mbw_v4_v5_parity.dump_fixture [tab2]
"""

import json
import sys

from commcare_connect.workflow.tests.mbw_v4_v5_parity.fixture import build_fixture, fixture_for_tab2


def main() -> None:
    tab2 = len(sys.argv) > 1 and sys.argv[1] == "tab2"
    fixture = fixture_for_tab2() if tab2 else build_fixture()
    print(json.dumps(fixture, indent=2, default=str))


if __name__ == "__main__":
    main()
