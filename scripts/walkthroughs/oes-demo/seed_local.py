"""Seed the OES demo into THIS machine's database, the way ensure_demo.py seeds labs.

Runs the very same driver (`ensure_demo.driver_source`) through
``manage.py shell`` on stdin -- so there is no argv ceiling to fit under, and
no second copy of the orchestration to drift from the one labs runs.

    python scripts/walkthroughs/oes-demo/seed_local.py --drive-folder <folder-id> --purge

Run it with the labs virtualenv on PATH (``make`` targets resolve it; or
activate the main checkout's ``.venv``), from anywhere in the checkout.

``--purge`` clears the four OES scopes first through
``SupplyDataAccess.purge()``, the domain's own wholesale delete. That refuses
any program without a REGISTERED labs-only opportunity under it
(``supply_chain/scopes.py``), so it can only ever clear demo data -- and the
driver's own "refusing to seed: these scopes already hold supply rows" guard
still stands behind it for anything the purge did not take.

The local database is often SHARED by every worktree on the machine. Purging
it clears the demo for all of them; say so to anyone else seeding there.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import ensure_demo  # noqa: E402

# Spliced in immediately after the driver reads SCOPES, so the purge runs in
# the same process, against the same scope list, before the emptiness guard.
PURGE = """
from connect_labs.labs.access.scopes import SYSTEM as _SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess as _Access

for _name, _scope in SCOPES.items():
    _counts = _Access(access_token="local", program_id=_scope["program_id"], caller=_SYSTEM).purge()
    print("purged %s (program %d): %s" % (_name, _scope["program_id"], _counts or "nothing"))
"""
ANCHOR = 'SCOPES = _seed["SCOPES"]\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drive-folder", required=True, help="the Drive folder id holding the seed document")
    parser.add_argument("--filename", default="oes-demo.json", help="the document in that folder")
    parser.add_argument("--purge", action="store_true", help="clear the four OES scopes first (labs-only data only)")
    args = parser.parse_args()

    driver = ensure_demo.driver_source(args.drive_folder, args.filename)
    if args.purge:
        if ANCHOR not in driver:
            sys.exit("the driver no longer reads SCOPES where seed_local expects to splice the purge")
        driver = driver.replace(ANCHOR, ANCHOR + PURGE, 1)

    result = subprocess.run(
        [sys.executable, "manage.py", "shell"],
        input=driver,
        text=True,
        cwd=REPO,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.local"),
        },
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
