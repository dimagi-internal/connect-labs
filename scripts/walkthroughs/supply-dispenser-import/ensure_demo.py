"""Setup for the `supply-dispenser-import` walkthrough: dispensers held at customs.

Reseeds labs-only programme 10604 on labs prod before every render, because the
narrative is state-mutating: on camera the programme officer attaches the import
documents and records the landing charges, and the distributor records the
arrival, the two broken units and the releases to project sites. A second take
must find the consignment exactly as the first one did -- sitting at customs,
weeks late, one document of four on file.

Two steps:

1. **Reset** (`--no-reset` skips it). The programme's supply rows are purged
   through `SupplyDataAccess.purge()`, which refuses any programme that is not a
   registered labs-only scope. There is no MCP tool for a purge -- deliberately:
   a wholesale delete is not an operation -- so it runs server-side through
   `aws ecs execute-command` on the labs worker (AWS profile `labs`).
2. **Seed** through the `connect_labs` MCP only, i.e. the same operation registry
   and schemas the UI and API use. No private write path.

Outputs go to `outputs.json` beside this file (gitignored): the ids the scenes
navigate to and today's date for the dates typed on camera.

The distributor gets its own update link, covering the order and the three
places the dispensers rest (its warehouse and the two project sites). On
camera it records the goods-received note and the release notes through that
link, without a labs login; the path lands in `outputs.json` and is never
printed or committed. The documents are files: the airway bill is uploaded
here, and the other three are uploaded on camera from their checklist lines
(`files/` beside this script holds the invented PDFs).

THIS REPOSITORY IS PUBLIC. Every organisation, person and amount here is
invented. The chain is the one the programme team described for chlorine
dispensers (donor-imported in kind, courier, customs, distributor warehouse,
project sites); none of their real names or prices appear.

    python scripts/walkthroughs/supply-dispenser-import/ensure_demo.py
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
OUTPUTS = HERE / "outputs.json"

PROGRAM_ID = 10604
LABEL = "supply-dispenser-import"

# ---- interpreter -----------------------------------------------------------
# The recorder runs `setup.command` under canopy's interpreter, which may not
# have httpx. Re-exec through the labs venv (the main clone's, shared by every
# worktree) exactly once.


def _labs_python() -> str | None:
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    candidate = (REPO_ROOT / common).resolve().parent / ".venv/bin/python"
    return str(candidate) if candidate.exists() else None


if os.environ.get("_DISPENSER_SETUP_REEXEC") != "1":
    try:
        import httpx  # noqa: F401,F811
    except ImportError:
        python = _labs_python()
        if python is None:
            sys.exit("ensure_demo: no interpreter with httpx found (labs venv missing)")
        os.environ["_DISPENSER_SETUP_REEXEC"] = "1"
        os.execv(python, [python, __file__, *sys.argv[1:]])

import httpx  # noqa: E402,F811

sys.path.insert(0, str(REPO_ROOT))
from scripts.walkthroughs._mcp_client import call, session, token  # noqa: E402

# ---- reset -----------------------------------------------------------------

PURGE = f"""
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.update_links.models import UpdateLink
from connect_labs.supply_chain.alerts.models import AlertSubscription
# Links and subscriptions are programme-scoped but outside purge()'s list on the
# deployed build; clear them first so a reseed does not stack stale links.
UpdateLink.objects.filter(program_id={PROGRAM_ID}).delete()
AlertSubscription.objects.filter(program_id={PROGRAM_ID}).delete()
print("PURGED", SupplyDataAccess(program_id={PROGRAM_ID}, caller=SYSTEM).purge())
"""


def reset() -> None:
    env = {**os.environ, "AWS_PROFILE": os.environ.get("AWS_PROFILE", "labs"), "AWS_REGION": "us-east-1"}
    tasks = subprocess.run(
        [
            "aws",
            "ecs",
            "list-tasks",
            "--cluster",
            "labs-jj-cluster",
            "--service-name",
            "labs-jj-worker",
            "--desired-status",
            "RUNNING",
            "--query",
            "taskArns[-1]",
            "--output",
            "text",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout.strip()
    b64 = base64.b64encode(PURGE.encode()).decode()
    command = f"python manage.py shell -c \"exec(__import__('base64').b64decode('{b64}').decode())\""
    # The session must keep stdin OPEN until the command finishes: an EOF on
    # stdin ends an interactive ECS exec session before the shell has run.
    # So read its output line by line and stop at the report.
    proc = subprocess.Popen(
        [
            "aws",
            "ecs",
            "execute-command",
            "--cluster",
            "labs-jj-cluster",
            "--task",
            tasks,
            "--container",
            "worker",
            "--interactive",
            "--command",
            command,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    seen, line = [], None
    try:
        for raw in proc.stdout:
            seen.append(raw)
            if "PURGED" in raw:
                line = raw[raw.index("PURGED") :].strip()
                break
            if "Traceback" in raw or "Error" in raw:
                seen.extend(proc.stdout.readlines()[:40])
                break
    finally:
        proc.kill()
    if line is None:
        sys.exit("ensure_demo: purge did not report back\n" + "".join(seen)[-3000:])
    print(line)


# ---- seed ------------------------------------------------------------------


class Seeder:
    def __init__(self, client: httpx.Client):
        self.client = client
        self.headers = session(client, token())

    def op(self, name: str, **args):
        args.setdefault("program_id", PROGRAM_ID)
        # MCP writes are rate-limited per user (30/min), and the sibling supply
        # narratives seed as the same user. Back off rather than fail a render.
        for _attempt in range(12):
            result, is_error = call(self.client, self.headers, f"supply_chain_{name}", args)
            if not (is_error and "rate limit" in str(result).lower()):
                break
            time.sleep(10)
        if is_error:
            sys.exit(f"ensure_demo: {name} failed: {result}")
        return result

    def ensure_registered(self) -> None:
        """Programme 10604 must be a registered labs-only scope before anything else.

        Registration is idempotent in effect but not in call -- every
        `synthetic_create_labs_only` mints a new opportunity -- so only register
        when the programme is not yet reachable.
        """
        _, is_error = call(self.client, self.headers, "supply_chain_chain_summary", {"program_id": PROGRAM_ID})
        if not is_error:
            return
        result, is_error = call(
            self.client,
            self.headers,
            "synthetic_create_labs_only",
            {
                "label": LABEL,
                "gdrive_folder_id": "none",
                "org_name": "Sahel Community Health Initiative",
                "program_name": "Chlorine dispensers (synthetic)",
                "program_id": PROGRAM_ID,
                "allowed_domains": ["@dimagi.com", "@dimagi-ai.com"],
                "notes": "DDD narrative supply-dispenser-import; supply data only",
            },
        )
        if is_error:
            sys.exit(f"ensure_demo: could not register programme {PROGRAM_ID}: {result}")


def seed(s: Seeder) -> dict:
    today = dt.date.today()
    days = lambda n: (today - dt.timedelta(days=n)).isoformat()  # noqa: E731

    # --- organisations (labs-wide; upsert by slug) ---------------------------
    def org(slug, name, **extra):
        return s.op("org_upsert", data={"slug": slug, "name": name, **extra})["id"]

    donor = org("clearwater-action", "ClearWater Action", notes="Donor NGO; supplies chlorine and dispensers in kind")
    distributor = org("harmattan-health-supplies", "Harmattan Health Supplies", country="NG")
    llo = org("sahel-community-health-initiative", "Sahel Community Health Initiative", country="NG")
    courier = org("kestrel-express-couriers", "Kestrel Express Couriers")
    clearing = org("lagoon-clearing-forwarding", "Lagoon Clearing & Forwarding", country="NG")
    org("port-customs-duty-account", "Port customs (duty account)", country="NG")
    # The programme itself is the buyer of record: the org the other supply
    # narratives in this family use for "us".
    us = org("dimagi", "Dimagi")

    # --- the donor, as the supplier of record for an in-kind contract -------
    supplier = s.op(
        "supplier_create",
        data={"name": "ClearWater Action", "type": "donor", "status": "awarded", "org_id": donor},
    )["id"]

    # --- what is being imported ---------------------------------------------
    s.op(
        "commodity_upsert",
        data={
            "slug": "chlorine-dispenser",
            "name": "Chlorine dispenser",
            "category": "equipment",
            "base_unit": "unit",
            "pack_unit": "unit",
            "base_per_pack": 1,
        },
    )
    s.op(
        "commodity_upsert",
        data={
            "slug": "dispenser-spare-parts",
            "name": "Dispenser spare-parts set",
            "category": "equipment",
            "base_unit": "set",
            "pack_unit": "set",
            "base_per_pack": 1,
        },
    )
    item = s.op(
        "item_upsert",
        data={
            "sku": "cwa-dispenser-standard",
            "name": "ClearWater standard dispenser, with spare-parts set",
            "commodity_slug": "chlorine-dispenser",
            "base_unit": "unit",
            "pack_unit": "unit",
            "base_per_pack": 1,
            "stock_class": "durable",
            "components": [
                {"commodity_slug": "chlorine-dispenser", "quantity": "1", "base_unit": "unit"},
                {"commodity_slug": "dispenser-spare-parts", "quantity": "1", "base_unit": "set"},
            ],
        },
    )["id"]

    # --- where it will be held ------------------------------------------------
    def point(slug, name, kind, managed_by, area):
        return s.op(
            "supply_point_upsert",
            data={
                "slug": slug,
                "name": name,
                "kind": kind,
                "managed_by_org_id": managed_by,
                "admin_area": area,
                "source": "we_recorded",
            },
        )["id"]

    warehouse = point("harmattan-warehouse", "Harmattan warehouse", "central_store", distributor, "Kano")
    sites = {
        "dawaki": point("dawaki-project-site", "Dawaki project site", "facility", llo, "Kano"),
        "rimi": point("rimi-project-site", "Rimi project site", "facility", llo, "Kano"),
    }

    # --- the in-kind order: no price, but a real physical chain --------------
    contract = s.op(
        "contract_create",
        data={
            "supplier_id": supplier,
            "item_id": item,
            "commodity_slug": "chlorine-dispenser",
            "reference": "CWA-DISP-2026-01",
            "status": "confirmed",
            "consideration": "in_kind",
            "quantity": "120",
            "quantity_unit": "unit",
            "currency": "USD",
            "buyer_of_record": "programme_org",
            "buyer_org_id": us,
            "delivery_supply_point_id": warehouse,
            "promised_lead_time_days": 30,
            "source": "we_recorded",
        },
    )["id"]

    # --- the consignment, sitting at customs, weeks past its expected date --
    shipment = s.op(
        "shipment_record",
        data={
            "contract_id": contract,
            "reference": "KEX-AWB-40981",
            "status": "at_customs",
            "dispatched_on": days(40),
            "expected_on": days(19),
            "carrier": "Kestrel Express Couriers",
            "lines": [{"item_id": item, "quantity": "120", "quantity_unit": "unit"}],
            "required_documents": [
                {"kind": "airway_bill", "owed_by_org_id": courier},
                {"kind": "packing_list", "owed_by_org_id": donor},
                {"kind": "commercial_invoice", "owed_by_org_id": donor},
                {"kind": "customs_declaration", "owed_by_org_id": clearing},
            ],
            "source": "we_recorded",
        },
    )["id"]
    # The courier's airway bill is already on file when the story opens: a
    # file, uploaded, like the three Halima files on camera.
    airway_bill = HERE / "files" / "airway-bill-KEX-AWB-40981.pdf"
    s.op(
        "document_attach",
        data={
            "kind": "airway_bill",
            "title": "Airway bill KEX-AWB-40981",
            "filename": airway_bill.name,
            "content_type": "application/pdf",
            "content_base64": base64.b64encode(airway_bill.read_bytes()).decode(),
            "shipment_id": shipment,
            "source": "document",
        },
    )

    # Harmattan reports the arrival, the inspection and the releases itself,
    # through a link that covers this order and the three places it can move
    # the dispensers between -- nothing else in the programme.
    link = s.op(
        "update_link_issue",
        data={
            "org_id": distributor,
            "contract_ids": [contract],
            "supply_point_ids": [warehouse, sites["dawaki"], sites["rimi"]],
            "label": "Harmattan — ClearWater dispensers",
            "expires_in_days": 30,
        },
    )

    return {
        "program_id": PROGRAM_ID,
        "contract_id": contract,
        "shipment_id": shipment,
        "item_id": item,
        "warehouse_id": warehouse,
        "distributor_link_path": _link_path(link),
        # Dates typed on camera: today's, so a re-render never files a receipt
        # or a charge in the past relative to the consignment it closes.
        "today": today.isoformat(),
    }


def _link_path(link: dict) -> str:
    """The update link as a site-relative path, so the recipe's base_url applies."""
    raw = link.get("url") or ""
    return "/" + raw.split("://", 1)[1].split("/", 1)[1] if "://" in raw else raw


def main() -> None:
    if "--no-reset" not in sys.argv:
        reset()
    with httpx.Client(timeout=300) as client:
        seeder = Seeder(client)
        seeder.ensure_registered()
        outputs = seed(seeder)
    OUTPUTS.write_text(json.dumps(outputs, indent=2) + "\n")
    print(json.dumps({k: ("<redacted>" if k.endswith("_link_path") else v) for k, v in outputs.items()}))


if __name__ == "__main__":
    main()
