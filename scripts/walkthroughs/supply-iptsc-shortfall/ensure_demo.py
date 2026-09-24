"""Setup for the `supply-iptsc-shortfall` DDD narrative.

The world the narrative opens on: the programme ordered 700 IPTSc three-day
treatment packets from its in-country distributor, months ago, with a promised
lead time that has now passed. The distributor has invoiced for what it could
source. Nothing has been dispatched or received yet, and the partner has not
bought anything locally -- those are the acts the scenes PERFORM on camera.

So every render must start from exactly this state, which means undoing what
the previous take recorded. There is no delete operation for a ledger (it is
append-only by design), so the reset is `SupplyDataAccess.purge()` -- which
refuses anything but a registered labs-only programme -- run inside the web
container by ECS exec. Everything after the purge goes in through the ordinary
operations over the labs MCP, the same schemas the UI and API enforce.

THIS REPOSITORY IS PUBLIC. Every organisation, supplier, product, price and
reference below is invented. The kit composition is illustrative, not a dosing
protocol.

Writes `.run_ids.json` beside this file (gitignored): the ids the recipe's
scene urls need, and the distributor's update-link path. The raw token lives
only in that local file, never in a commit.

    python scripts/walkthroughs/supply-iptsc-shortfall/ensure_demo.py
    python scripts/walkthroughs/supply-iptsc-shortfall/ensure_demo.py --no-reset
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _reexec_in_labs_venv() -> None:
    """Run under the labs virtualenv when the caller's interpreter lacks httpx.

    The recorder runs this as a subprocess of the canopy runtime, whose
    interpreter has none of labs' dependencies. An emdash worktree has no venv
    of its own; it borrows the main clone's, found by asking git where the
    common .git lives.
    """
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    venv_python = (REPO_ROOT / common).resolve().parent / ".venv/bin/python" if common else None
    if venv_python is None or not venv_python.exists() or Path(sys.executable).resolve() == venv_python.resolve():
        raise SystemExit("httpx is not importable and no labs .venv was found to re-run under")
    os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


try:
    import httpx
except ImportError:  # pragma: no cover - depends on the caller's interpreter
    _reexec_in_labs_venv()

from scripts.walkthroughs._mcp_client import call, session, token  # noqa: E402

# `IPTSC_PROGRAMME_ID` films a take in a FRESH labs-only programme instead:
# registered here if it is not yet, and never purged. That is the way to render
# when the ECS purge is unavailable (no AWS session), since a take cannot be
# filmed on top of the previous one's dispatches and receipts.
PROGRAMME_ID = int(os.environ.get("IPTSC_PROGRAMME_ID", "10602"))
OUT = HERE / ".run_ids.json"

# Lead time and signing date put the distributor's order past due on the day
# of the render: signed 15 July with 45 days promised is due 29 August.
SIGNED_ON = "2026-07-15"
LEAD_TIME_DAYS = 45

PURGE_SCRIPT = f"""
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.update_links.models import UpdateLink
print("purged", SupplyDataAccess(access_token="setup", program_id={PROGRAMME_ID}, caller=SYSTEM).purge())
print("links", UpdateLink.objects.filter(program_id={PROGRAMME_ID}).delete())
"""


class Seeder:
    def __init__(self, client: httpx.Client):
        self.c = client
        self.h = session(client, token())

    def op(self, name: str, **args):
        args.setdefault("program_id", PROGRAMME_ID)
        # Writes are rate-limited per user (30/min), and the sibling supply
        # walkthroughs seed as the same user, so a limit is waited out.
        for attempt in range(8):
            result, is_error = call(self.c, self.h, f"supply_chain_{name}", args)
            if not (is_error and "rate limit" in str(result).lower()):
                break
            time.sleep(10 + 5 * attempt)
        if is_error:
            raise SystemExit(f"{name} failed: {result}")
        if isinstance(result, dict) and set(result) == {"value"}:
            return result["value"]
        return result


def ensure_registered(s: Seeder) -> None:
    """A programme other than the default must be a registered labs-only scope.

    Every `synthetic_create_labs_only` call mints a new opportunity, so only
    register when the programme is not reachable yet.
    """
    _, is_error = call(s.c, s.h, "supply_chain_chain_summary", {"program_id": PROGRAMME_ID})
    if not is_error:
        return
    result, is_error = call(
        s.c,
        s.h,
        "synthetic_create_labs_only",
        {
            "label": f"IPTSc shortfall walkthrough take ({PROGRAMME_ID})",
            "gdrive_folder_id": "none",
            "org_name": "Sahel Community Health Initiative",
            "program_name": "IPTSc shortfall (synthetic)",
            "program_id": PROGRAMME_ID,
            "allowed_domains": ["@dimagi.com", "@dimagi-ai.com"],
            "notes": "DDD narrative supply-iptsc-shortfall; supply data only",
        },
    )
    if is_error:
        raise SystemExit(f"could not register programme {PROGRAMME_ID}: {result}")


def purge_via_ecs() -> None:
    """Reset the programme inside the deployed web container."""
    env = {**os.environ, "AWS_PROFILE": os.environ.get("AWS_PROFILE", "labs"), "AWS_REGION": "us-east-1"}
    task = subprocess.run(
        [
            "aws",
            "ecs",
            "list-tasks",
            "--cluster",
            "labs-jj-cluster",
            "--service-name",
            "labs-jj-web",
            "--desired-status",
            "RUNNING",
            "--query",
            "taskArns[0]",
            "--output",
            "text",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    b64 = base64.b64encode(PURGE_SCRIPT.encode()).decode()
    command = f"python manage.py shell -c \"exec(__import__('base64').b64decode('{b64}').decode())\""
    # The interactive session ends the moment its stdin closes, before the
    # shell command has run; a sleeping pipe keeps it open long enough.
    keepalive = subprocess.Popen(["sleep", "45"], stdout=subprocess.PIPE)
    try:
        proc = subprocess.run(
            [
                "aws",
                "ecs",
                "execute-command",
                "--cluster",
                "labs-jj-cluster",
                "--task",
                task,
                "--container",
                "web",
                "--interactive",
                "--command",
                command,
            ],
            env=env,
            stdin=keepalive.stdout,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        keepalive.kill()
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(("purged", "links"))]
    if proc.returncode != 0 or not lines:
        raise SystemExit(f"purge failed ({proc.returncode}): {proc.stdout[-800:]} {proc.stderr[-800:]}")
    print("\n".join(lines))


def org_id(s: Seeder, slug: str, name: str, **extra) -> int:
    return s.op("org_upsert", data={"slug": slug, "name": name, "country": "NG", **extra})["id"]


def seed(s: Seeder) -> dict:
    us = next(o for o in s.op("org_list") if o["slug"] == "dimagi")["id"]
    harmattan_org = org_id(
        s,
        "harmattan-health-supplies",
        "Harmattan Health Supplies",
        notes="Invented in-country distributor for the supply use-case walkthroughs.",
    )
    schi = org_id(
        s,
        "sahel-community-health-initiative",
        "Sahel Community Health Initiative",
        short_name="SCHI",
        notes="Invented local implementing partner for the supply use-case walkthroughs.",
    )

    # --- the packet and what is inside it -----------------------------------
    s.op(
        "commodity_upsert",
        data={
            "slug": "dihydroartemisinin-piperaquine",
            "name": "Dihydroartemisinin-piperaquine tablet",
            "category": "antimalarial",
            "base_unit": "tablet",
            "pack_unit": "blister",
            "base_per_pack": 3,
        },
    )
    s.op(
        "commodity_upsert",
        data={
            "slug": "iptsc-dosing-card",
            "name": "IPTSc dosing card",
            "category": "consumable",
            "base_unit": "card",
            "pack_unit": "bundle",
            "base_per_pack": 100,
        },
    )
    s.op(
        "commodity_upsert",
        data={
            "slug": "iptsc-treatment-packet",
            "name": "IPTSc three-day treatment packet",
            "category": "antimalarial",
            "base_unit": "packet",
            "pack_unit": "carton",
            "base_per_pack": 50,
            "course_definition": {
                "base_units_per_course": 1,
                "days_per_course": 3,
                "source": "programme team: one standard packet is one three-day course",
            },
        },
    )
    item = s.op(
        "item_upsert",
        data={
            "sku": "iptsc-3day-packet",
            "name": "IPTSc three-day packet (standard)",
            "commodity_slug": "iptsc-treatment-packet",
            "base_unit": "packet",
            "pack_unit": "carton",
            "base_per_pack": 50,
            "shelf_life_months": 24,
            "status": "active",
            # Illustrative contents: a three-day course of tablets and the card
            # a teacher uses to tick off each day's dose.
            "components": [
                {"commodity_slug": "dihydroartemisinin-piperaquine", "quantity": "9", "base_unit": "tablet"},
                {"commodity_slug": "iptsc-dosing-card", "quantity": "1", "base_unit": "card"},
            ],
            "one_course_is": "base_unit",
        },
    )

    # --- who sells it ---------------------------------------------------------
    harmattan = s.op(
        "supplier_create", data={"name": "Harmattan Health Supplies", "type": "distributor", "status": "awarded"}
    )
    tamarind = s.op(
        "supplier_create", data={"name": "Tamarind Pharmacy Wholesale", "type": "trader", "status": "responsive"}
    )

    # --- where it goes --------------------------------------------------------
    store = s.op(
        "supply_point_upsert",
        data={
            "slug": "schi-district-store",
            "name": "SCHI district store",
            "kind": "regional_store",
            "managed_by_org_id": schi,
            "admin_area": "Birnin Tudu",
            "source": "we_recorded",
        },
    )

    # --- the order, and the distributor's invoice for what it could find ------
    order = s.op(
        "contract_create",
        data={
            "supplier_id": harmattan["id"],
            "commodity_slug": "iptsc-treatment-packet",
            "item_id": item["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": us,
            "reference": "IPTSC-PO-0715",
            "signed_on": SIGNED_ON,
            "status": "confirmed",
            "consideration": "priced",
            "currency": "USD",
            "quantity": "700",
            "quantity_unit": "packet",
            "unit_price": "1.80",
            "unit_price_unit": "per_base_unit",
            "freight_basis": "included",
            "duties_basis": "included",
            "vat_basis": "included",
            "delivery_supply_point_id": store["id"],
            "promised_lead_time_days": LEAD_TIME_DAYS,
            "source": "we_recorded",
        },
    )
    s.op(
        "invoice_record",
        data={
            "contract_id": order["id"],
            "reference": "HHS-INV-2291",
            "issued_on": "2026-09-19",
            "status": "received",
            "currency": "USD",
            "amount": "810.00",
            "quantity_billed": "450",
            "quantity_unit": "packet",
            "source": "supplier_reported",
            "recorded_by_org_id": harmattan_org,
        },
    )

    # Two update links, one per outside party. The distributor's names the one
    # order it dispatches against. The partner's is issued as "Everything
    # involving" SCHI: resolved at every request, so the local purchase it
    # makes later (scene 6) is on its link the moment it exists, and only
    # receipts are offered on it -- SCHI buys and receives, it does not supply.
    distributor_link = _link_path(
        s.op(
            "update_link_issue",
            data={
                "org_id": harmattan_org,
                "contract_ids": [order["id"]],
                "label": "Harmattan — IPTSc order",
                "expires_in_days": 30,
            },
        )
    )
    partner_link = _link_path(
        s.op(
            "update_link_issue",
            data={
                "org_id": schi,
                "coverage": "organisation",
                "label": "SCHI — everything involving us",
                "expires_in_days": 30,
            },
        )
    )

    return {
        "program_id": PROGRAMME_ID,
        # The render's own date, so a receipt typed on camera is dated with the
        # one recorded through the partner's link (which has no date field).
        "today": time.strftime("%Y-%m-%d"),
        "order_id": order["id"],
        "item_id": item["id"],
        "store_id": store["id"],
        "harmattan_supplier_id": harmattan["id"],
        "tamarind_supplier_id": tamarind["id"],
        "schi_org_id": schi,
        "distributor_link_path": distributor_link,
        "partner_link_path": partner_link,
    }


def _link_path(link: dict) -> str:
    """The update link as a site-relative path, so the recipe's base_url applies."""
    raw = link.get("url") or ""
    return "/" + raw.split("://", 1)[1].split("/", 1)[1] if "://" in raw else raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()
    with httpx.Client(timeout=600) as client:
        s = Seeder(client)
        if PROGRAMME_ID != 10602:
            ensure_registered(s)
        if not args.no_reset and s.op("contract_list"):
            purge_via_ecs()
        realized = seed(s)
    OUT.write_text(json.dumps(realized, indent=2))
    shown = {k: ("<redacted>" if k.endswith("_link_path") else v) for k, v in realized.items()}
    print(json.dumps(shown, indent=2))


if __name__ == "__main__":
    main()
