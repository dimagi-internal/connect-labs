"""Setup for the `supply-chlorine-stopgap` DDD walkthrough (labs-only programme 10603).

The story: a donor NGO (ClearWater Action) supplies the programme's dispenser
chlorine IN KIND, and its latest consignment is about ninety days late. The
store that refills the dispensers is under its two-month floor. The programme
asks its in-country distributor (Harmattan Health Supplies) for nationally
registered chlorine as a stop-gap; the distributor's quote carries the
product's registration certificate. The award needs the funder's approval
before an order can rest on it.

This script builds the world as it stands the morning the programme lead sits
down to deal with it: the late donation, the store's consumption history, the
stop-gap round with the distributor's quote in. Everything AFTER that -- the
alert to the donor, the award, the approvals, the refused order, the order,
the supplier link and the delivery recorded through it -- is performed ON
CAMERA by the walkthrough.

THIS REPOSITORY IS PUBLIC. Every organisation, person, product, manufacturer,
registration number and price below is invented.

Two phases:

1. **Reset** (default; `--no-reset` skips it). Every take awards, approves,
   orders and receives, so a second take must start from the same world as the
   first. There is deliberately no MCP tool that purges a programme, so the
   reset runs `SupplyDataAccess.purge()` inside the deployed web task over ECS
   Exec (AWS profile `labs`). `purge()` refuses anything but a registered
   labs-only programme. The programme's update links and alert subscriptions go
   with it: `purge()` does not reach them.

2. **Seed**, through the connect_labs MCP only -- the same operation registry,
   schemas and provenance stamping as the web UI. No private write path.

Every date is relative to the day the script runs, so "ninety days late" stays
true whenever the film is made.

    python scripts/walkthroughs/supply-chlorine-stopgap/seed.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROGRAMME_ID = 10603
MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")
OUTPUTS = Path(__file__).with_name("outputs.json")

ROUND_LABEL = "Chlorine stop-gap — nationally registered"
STORE_SLUG = "sahel-chi-kano-chlorine-store"

# How the store has been running. 80 jerry cans a month over the last ninety
# days, 60 left: under a month of cover against a two-month floor.
CONSUMPTION = ((85, 80), (55, 80), (25, 80))  # (days ago, jerry cans)
PRIOR_DELIVERY = 300  # jerry cans, the last donation that did arrive
STOPGAP_CANS = 90  # a bridge the funder can approve, not a replacement for the donation
DONATION_CANS = 400


def today():
    """The server's today. Labs runs in UTC, and every check dates itself from
    the server's calendar, so a late-evening seed in another timezone must not
    make "ninety days late" read as eighty-nine."""
    return datetime.now(timezone.utc).date()


def today_minus(days: int) -> str:
    return (today() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# A stdlib MCP-over-HTTP client. The recorder runs `setup:` under the canopy
# runtime's interpreter, so this must not depend on the labs venv.
# ---------------------------------------------------------------------------


def _token() -> str:
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    for cfg in (Path.home() / ".claude.json", Path.home() / ".claude" / "mcp.json"):
        if not cfg.exists():
            continue
        data = json.loads(cfg.read_text())
        for name, spec in (data.get("mcpServers") or data.get("servers") or {}).items():
            if "connect_labs" in name or "labs" in name:
                headers = spec.get("headers") or {}
                auth = headers.get("Authorization") or headers.get("authorization") or ""
                if auth.startswith("Bearer "):
                    return auth[len("Bearer ") :]
    sys.exit("No MCP token: set LABS_MCP_TOKEN or configure connect_labs in ~/.claude.json")


def _parse(body: str) -> dict:
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except ValueError:
                pass
    try:
        return json.loads(body)
    except ValueError:
        return {"_raw": body[:400]}


class Mcp:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        _, response_headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "supply-chlorine-stopgap-seed", "version": "1"},
                },
            }
        )
        session_id = response_headers.get("mcp-session-id")
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, payload):
        request = urllib.request.Request(
            MCP_URL, data=json.dumps(payload).encode(), headers=self.headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            return response.read().decode(), headers

    def op(self, name: str, **arguments):
        """Call `supply_chain_<name>`, waiting out the per-user MCP write-rate limit.

        The sibling supply narratives seed as the same user, so a burst from any
        of them can land on this one. A 5xx while a deploy rolls is waited out too.
        Neither retry can double a write: a rate-limited call was refused before it
        ran, and a 502 from the load balancer never reached a task.
        """
        for attempt in range(8):
            try:
                return self._op(name, **arguments)
            except RuntimeError as error:
                if "rate limit" not in str(error).lower() or attempt == 7:
                    raise
                print(f"  {name}: rate-limited, waiting", file=sys.stderr)
                time.sleep(15)
            except urllib.error.HTTPError as error:
                # A deploy rolling its web tasks answers 502/503 for a minute.
                if error.code < 500 or attempt == 7:
                    raise
                print(f"  {name}: HTTP {error.code}, waiting for labs", file=sys.stderr)
                time.sleep(15)

    def _op(self, name: str, **arguments):
        arguments.setdefault("program_id", PROGRAMME_ID)
        body, _ = self._post(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": f"supply_chain_{name}", "arguments": arguments},
            }
        )
        result = _parse(body).get("result") or {}
        content = result.get("structuredContent")
        if content is None:
            parts = result.get("content") or []
            text = parts[0].get("text", "") if parts else body[:400]
            try:
                content = json.loads(text)
            except ValueError:
                content = text
        if result.get("isError"):
            raise RuntimeError(f"{name} refused: {str(content)[:600]}")
        if isinstance(content, dict) and set(content) == {"value"}:
            return content["value"]
        return content


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

PURGE = f"""
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.alerts.models import AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.update_links.models import UpdateLink
links = UpdateLink.objects.filter(program_id={PROGRAMME_ID}).delete()[0]
alerts = AlertSubscription.objects.filter(program_id={PROGRAMME_ID}).delete()[0]
access = SupplyDataAccess(access_token="walkthrough-reset", program_id={PROGRAMME_ID}, caller=SYSTEM)
print("PURGED", {PROGRAMME_ID}, access.purge(), "links", links, "alerts", alerts)
"""


def reset() -> None:
    """Purge the programme in the deployed web container.

    ECS Exec first (fast, needs a live `labs` AWS SSO session). When that
    session has expired, the same code runs as a one-off Fargate task through
    the `run-labs-command.yml` workflow, which authenticates with GitHub OIDC
    rather than local credentials -- slower (about two minutes), but a render
    must not depend on somebody having signed in to AWS that morning.
    """
    encoded = base64.b64encode(PURGE.encode()).decode()
    code = f"exec(__import__('base64').b64decode('{encoded}').decode())"
    line = _reset_over_ecs_exec(code) or _reset_over_workflow(code)
    if line is None:
        sys.exit("reset failed over both ECS Exec and the run-labs-command workflow")
    print(f"reset: {line.strip()}", file=sys.stderr)


def _reset_over_ecs_exec(code: str) -> str | None:
    env = {**os.environ, "AWS_PROFILE": os.environ.get("AWS_PROFILE", "labs"), "AWS_REGION": "us-east-1"}
    listed = subprocess.run(
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
        capture_output=True,
        text=True,
        env=env,
    )
    if listed.returncode != 0:
        print(f"reset: ECS Exec unavailable ({listed.stderr.strip()[:160]})", file=sys.stderr)
        return None
    command = f'python manage.py shell -c "{code}"'
    # The session needs stdin held open until the command has finished, or it
    # closes before the output arrives.
    with subprocess.Popen(["sleep", "45"], stdout=subprocess.PIPE) as keepalive:
        out = subprocess.run(
            [
                "aws",
                "ecs",
                "execute-command",
                "--cluster",
                "labs-jj-cluster",
                "--task",
                listed.stdout.strip(),
                "--container",
                "web",
                "--interactive",
                "--command",
                command,
            ],
            stdin=keepalive.stdout,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        keepalive.kill()
    return next((ln for ln in out.stdout.splitlines() if f"PURGED {PROGRAMME_ID}" in ln), None)


WORKFLOW = "run-labs-command.yml"
REPO = "dimagi-internal/connect-labs"


def _reset_over_workflow(code: str) -> str | None:
    def runs() -> list[dict]:
        listed = subprocess.run(
            ["gh", "run", "list", "--repo", REPO, "--workflow", WORKFLOW, "-L", "10", "--json", "databaseId,status"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(listed.stdout)

    before = {run["databaseId"] for run in runs()}
    subprocess.run(
        ["gh", "workflow", "run", WORKFLOW, "--repo", REPO, "--ref", "main", "--field", f'command=shell -c "{code}"'],
        check=True,
    )
    print("reset: purging through the run-labs-command workflow (about two minutes)", file=sys.stderr)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        time.sleep(15)
        # A sibling may dispatch the same workflow; ours is whichever new run
        # prints this programme's PURGED line.
        for run in runs():
            if run["databaseId"] in before or run["status"] != "completed":
                continue
            log = subprocess.run(
                ["gh", "run", "view", str(run["databaseId"]), "--repo", REPO, "--log"],
                capture_output=True,
                text=True,
            ).stdout
            line = next((ln for ln in log.splitlines() if f"PURGED {PROGRAMME_ID}" in ln), None)
            if line:
                return "PURGED" + line.split("PURGED", 1)[1]
            before.add(run["databaseId"])
    return None


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def org(mcp: Mcp, slug: str, name: str, notes: str) -> int:
    """The organisation's id. Organisations are labs-wide and shared with the
    sibling supply narratives, so an existing one is reused as it stands, never
    rewritten or deleted."""
    for row in mcp.op("org_list"):
        if row["slug"] == slug:
            return row["id"]
    return mcp.op("org_upsert", data={"slug": slug, "name": name, "country": "NG", "notes": notes})["id"]


# The specimens live beside this script as PDFs (rendered from their Markdown
# sources by make_specimens.py). They are UPLOADED -- stored in labs' document
# bucket, exactly as a file chosen in the browser is -- not linked. The product
# registration is not seeded at all: the programme lead files it against the
# quote on camera.
DOCUMENTS = Path(__file__).with_name("documents")


def uploaded_document(title: str, filename: str) -> dict:
    return {
        "title": title,
        "filename": filename,
        "content_type": "application/pdf",
        "content_base64": base64.b64encode((DOCUMENTS / filename).read_bytes()).decode(),
    }


def seed(mcp: Mcp) -> dict:
    # --- who is involved ------------------------------------------------------
    harmattan = org(
        mcp,
        "harmattan-health-supplies",
        "Harmattan Health Supplies",
        "Invented in-country distributor for the supply walkthroughs.",
    )
    clearwater = org(mcp, "clearwater-action", "ClearWater Action", "Invented donor NGO for the supply walkthroughs.")
    sahel = org(
        mcp,
        "sahel-community-health-initiative",
        "Sahel Community Health Initiative",
        "Invented local implementing partner for the supply walkthroughs.",
    )
    northstar = org(mcp, "northstar-fund", "Northstar Fund", "Invented funder for the supply walkthroughs.")
    regulator = org(
        mcp,
        "national-products-registration-agency",
        "National Products Registration Agency",
        "Invented national product regulator for the supply walkthroughs.",
    )
    programme_org = next(o["id"] for o in mcp.op("org_list") if o["slug"] == "dimagi")

    # --- what is being bought -------------------------------------------------
    # Dispenser chlorine is dosed for one concentration; a solution outside the
    # band under- or over-chlorinates every jerry can a household fills.
    mcp.op(
        "commodity_upsert",
        data={
            "slug": "dispenser-chlorine",
            "name": "Dispenser chlorine solution",
            "category": "consumable",
            "base_unit": "L",
            "pack_unit": "jerry_can",
            "base_per_pack": 3,
            "shelf_life_months_minimum": 6,
            "spec_reference": "Programme dispenser dosing sheet (invented for the walkthrough)",
            "spec_requirements": [
                {
                    "field": "available_chlorine_percent",
                    "operator": ">=",
                    "value": 1.2,
                    "unit": "%",
                    "rationale": "the dispensers are calibrated for 1.25% available chlorine",
                },
                {
                    "field": "available_chlorine_percent",
                    "operator": "<=",
                    "value": 1.3,
                    "unit": "%",
                    "rationale": "above 1.3% the dose tastes strongly enough that households stop using it",
                },
            ],
        },
    )
    donated_item = mcp.op(
        "item_upsert",
        data={
            "sku": "clearwater-dispenser-chlorine-3l",
            "name": "ClearWater dispenser chlorine 1.25%, 3 L",
            "commodity_slug": "dispenser-chlorine",
            "manufacturer": "ClearWater Action",
            "base_unit": "L",
            "pack_unit": "jerry_can",
            "base_per_pack": 3,
            "shelf_life_months": 12,
            "spec_attributes": {"available_chlorine_percent": 1.25},
            "status": "active",
        },
    )["id"]
    registered_item = mcp.op(
        "item_upsert",
        data={
            "sku": "aquaguard-hypochlorite-1-25-3l",
            "name": "Aquaguard sodium hypochlorite 1.25%, 3 L",
            "commodity_slug": "dispenser-chlorine",
            "manufacturer": "Kaduna Chemical Works",
            "base_unit": "L",
            "pack_unit": "jerry_can",
            "base_per_pack": 3,
            "shelf_life_months": 12,
            "spec_attributes": {"available_chlorine_percent": 1.25},
            "status": "active",
        },
    )["id"]

    # --- who supplies it -------------------------------------------------------
    # ClearWater gives the chlorine; it does not sell it. A donor supplier says
    # so in every picker, and its orders are in kind.
    donor = mcp.op(
        "supplier_create",
        data={"name": "ClearWater Action", "type": "donor", "status": "awarded", "org_id": clearwater},
    )["id"]
    distributor = mcp.op(
        "supplier_create",
        data={
            "name": "Harmattan Health Supplies",
            "type": "distributor",
            "status": "quoting",
            "org_id": harmattan,
        },
    )["id"]

    # --- where it is used --------------------------------------------------------
    store = mcp.op(
        "supply_point_upsert",
        data={
            "slug": STORE_SLUG,
            "name": "Sahel CHI — Kano chlorine store",
            "kind": "regional_store",
            "managed_by_org_id": sahel,
            "admin_area": "Kano",
            "min_months_of_stock": "2",
            "max_months_of_stock": "4",
            "source": "we_recorded",
        },
    )["id"]

    # --- the donation that arrived --------------------------------------------------
    prior = mcp.op(
        "contract_create",
        data={
            "supplier_id": donor,
            "commodity_slug": "dispenser-chlorine",
            "item_id": donated_item,
            "buyer_of_record": "programme_org",
            "buyer_org_id": programme_org,
            "reference": "CWA-DON-2026-01",
            "status": "placed",
            "consideration": "in_kind",
            "quantity": PRIOR_DELIVERY,
            "quantity_unit": "jerry_can",
            "delivery_supply_point_id": store,
            "promised_lead_time_days": 60,
            "signed_on": today_minus(160),
            "source": "partner_reported",
            "recorded_by_org_id": clearwater,
        },
    )["id"]
    prior_shipment = mcp.op(
        "shipment_record",
        data={
            "contract_id": prior,
            "reference": "CWA-DON-2026-01/1",
            "status": "delivered",
            "dispatched_on": today_minus(106),
            "expected_on": today_minus(100),
            "carrier": "ClearWater Action",
            "lines": [
                {"item_id": donated_item, "batch": "CW-2605", "quantity": PRIOR_DELIVERY, "quantity_unit": "jerry_can"}
            ],
            "source": "supplier_reported",
            "recorded_by_org_id": clearwater,
        },
    )["id"]
    mcp.op(
        "document_attach",
        data={
            "kind": "certificate_of_analysis",
            "shipment_id": prior_shipment,
            "source": "supplier_reported",
            "recorded_by_org_id": clearwater,
            **uploaded_document(
                "ClearWater batch CW-2605 — certificate of analysis", "certificate-of-analysis-CW-2605.pdf"
            ),
        },
    )
    mcp.op(
        "receipt_record",
        data={
            "contract_id": prior,
            "shipment_id": prior_shipment,
            "supply_point_id": store,
            "reference": "GRN-KANO-0412",
            "received_on": today_minus(100),
            "lines": [
                {
                    "item_id": donated_item,
                    "batch": "CW-2605",
                    "expiry": (today() + timedelta(days=260)).isoformat(),
                    "quantity_accepted": PRIOR_DELIVERY,
                    "quantity_unit": "jerry_can",
                }
            ],
            "source": "partner_reported",
            "recorded_by_org_id": sahel,
        },
    )

    # --- the store has been dispensing it ---------------------------------------------
    for days_ago, cans in CONSUMPTION:
        mcp.op(
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": today_minus(days_ago),
                "from_supply_point_id": store,
                "item_id": donated_item,
                "commodity_slug": "dispenser-chlorine",
                "batch": "CW-2605",
                "quantity": cans,
                "quantity_unit": "jerry_can",
                "reference": f"Dispenser refills, month ending {today_minus(days_ago)}",
                "source": "partner_reported",
                "recorded_by_org_id": sahel,
            },
        )

    # --- the donation that did not arrive --------------------------------------------
    # Signed 150 days ago with a 60-day lead time; the consignment was due 90
    # days ago and nothing has come.
    late = mcp.op(
        "contract_create",
        data={
            "supplier_id": donor,
            "commodity_slug": "dispenser-chlorine",
            "item_id": donated_item,
            "buyer_of_record": "programme_org",
            "buyer_org_id": programme_org,
            "reference": "CWA-DON-2026-02",
            "status": "confirmed",
            "consideration": "in_kind",
            "quantity": DONATION_CANS,
            "quantity_unit": "jerry_can",
            "delivery_supply_point_id": store,
            "promised_lead_time_days": 60,
            "signed_on": today_minus(150),
            "source": "partner_reported",
            "recorded_by_org_id": clearwater,
        },
    )["id"]
    late_shipment = mcp.op(
        "shipment_record",
        data={
            "contract_id": late,
            "reference": "CWA-DON-2026-02/1",
            "status": "planned",
            "expected_on": today_minus(90),
            "carrier": "ClearWater Action",
            "lines": [{"item_id": donated_item, "quantity": DONATION_CANS, "quantity_unit": "jerry_can"}],
            "source": "supplier_reported",
            "recorded_by_org_id": clearwater,
        },
    )["id"]

    # --- the stop-gap round, with the distributor's quote in ---------------------------
    round_ = mcp.op(
        "tender_create",
        data={
            "label": ROUND_LABEL,
            "lines": [
                {"commodity_slug": "dispenser-chlorine", "quantity": STOPGAP_CANS, "quantity_unit": "jerry_can"}
            ],
            "delivery_point": {
                "name": "Sahel CHI — Kano chlorine store",
                "city": "Kano",
                "country": "NG",
                "country_name": "Nigeria",
                "incoterm_requested": "DAP",
            },
        },
    )
    round_id = round_["id"]
    mcp.op("tender_open", tender_id=round_id)
    outreach = mcp.op(
        "outreach_log",
        data={"tender_id": round_id, "supplier_id": distributor, "channel": "manual", "sent_on": today_minus(5)},
    )
    mcp.op(
        "outreach_update",
        outreach_id=outreach["id"],
        data={"responded": True, "response_kind": "quote"},
    )
    quote = mcp.op(
        "quote_record",
        data={
            "tender_id": round_id,
            "supplier_id": distributor,
            "item_id": registered_item,
            "commodity_slug": "dispenser-chlorine",
            "as_quoted_amount": "6800",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "NGN",
            "quantity_basis": STOPGAP_CANS,
            "quantity_basis_unit": "jerry_can",
            # Invented, and stated on the quote so the comparison can put it in
            # USD rather than guess a rate.
            "fx_rate_to_usd": "0.00065",
            "pack_spec_source": "trade_item_confirmed",
            "freight_basis": "included",
            "duties_basis": "included",
            "shelf_life_months_stated": 12,
            "lead_time_days": 7,
            "moq": 50,
            "moq_unit": "jerry_can",
            "validity_until": (today() + timedelta(days=21)).isoformat(),
            "received_on": today_minus(2),
        },
    )
    quote_id = quote["id"]
    mcp.op(
        "document_attach",
        data={
            "kind": "quotation",
            "quote_id": quote_id,
            "source": "supplier_reported",
            "recorded_by_org_id": harmattan,
            **uploaded_document("Harmattan quotation HHS-Q-2291", "quotation-HHS-Q-2291.pdf"),
        },
    )

    # --- a rival quote: cheaper, slower, and with no registration on file ----
    # The comparison ranks cheapest first, so this one leads it. Ngozi awards
    # Harmattan anyway, and her written reason is what the record keeps.
    rival_org = org(
        mcp,
        "gidan-ruwa-traders",
        "Gidan Ruwa Traders",
        "Invented chemical trader for the supply walkthroughs.",
    )
    rival_item = mcp.op(
        "item_upsert",
        data={
            "sku": "gidan-ruwa-hypochlorite-1-25-3l",
            "name": "Gidan Ruwa sodium hypochlorite 1.25%, 3 L",
            "commodity_slug": "dispenser-chlorine",
            "manufacturer": "Gidan Ruwa Traders",
            "base_unit": "L",
            "pack_unit": "jerry_can",
            "base_per_pack": 3,
            "shelf_life_months": 12,
            "spec_attributes": {"available_chlorine_percent": 1.25},
            "status": "active",
        },
    )["id"]
    rival = mcp.op(
        "supplier_create",
        data={"name": "Gidan Ruwa Traders", "type": "trader", "status": "quoting", "org_id": rival_org},
    )["id"]
    rival_outreach = mcp.op(
        "outreach_log",
        data={"tender_id": round_id, "supplier_id": rival, "channel": "manual", "sent_on": today_minus(5)},
    )
    mcp.op("outreach_update", outreach_id=rival_outreach["id"], data={"responded": True, "response_kind": "quote"})
    mcp.op(
        "quote_record",
        data={
            "tender_id": round_id,
            "supplier_id": rival,
            "item_id": rival_item,
            "commodity_slug": "dispenser-chlorine",
            "as_quoted_amount": "6100",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "NGN",
            "quantity_basis": STOPGAP_CANS,
            "quantity_basis_unit": "jerry_can",
            "fx_rate_to_usd": "0.00065",
            "pack_spec_source": "trade_item_confirmed",
            "freight_basis": "included",
            "duties_basis": "included",
            "shelf_life_months_stated": 12,
            "lead_time_days": 21,
            "moq": 50,
            "moq_unit": "jerry_can",
            "validity_until": (today() + timedelta(days=14)).isoformat(),
            "received_on": today_minus(1),
        },
    )

    return {
        "programme_id": PROGRAMME_ID,
        "tender_id": round_id,
        "quote_id": quote_id,
        "late_contract_id": late,
        "late_shipment_id": late_shipment,
        "store_id": store,
        "donor_org_id": clearwater,
        "distributor_org_id": harmattan,
        "funder_org_id": northstar,
        "regulator_org_id": regulator,
        "stopgap_cans": STOPGAP_CANS,
        "today": today().isoformat(),
        "stopgap_expiry": (today() + timedelta(days=365)).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-reset", action="store_true", help="Seed on top of whatever is there.")
    parser.add_argument("--outputs", default=str(OUTPUTS), help="Where to write the ${var} map.")
    args = parser.parse_args()

    if not args.no_reset:
        reset()
    outputs = seed(Mcp())
    Path(args.outputs).write_text(json.dumps(outputs, indent=2) + "\n")
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
