"""Setup for the `supply-test-kits` DDD walkthrough (labs-only programme 10605).

The story: a technical partner (Aqualytic) specifies free-chlorine test kits;
the in-country distributor (Harmattan Health Supplies) quotes two kits of the
same composition; one meets the specification and one does not. Everything
AFTER the quotes -- the award, the technical partner's approval and its own
update link, the order, and what the distributor records through its link --
is performed ON CAMERA by the walkthrough, so this script deliberately stops
at "two quotes are in".

One thing is set up here rather than filmed: Harmattan's standing update
link, which follows the organisation (coverage `organisation`) so it covers
the order placed on camera without being reissued -- the way a distributor
the programme buys from every quarter actually holds one. Its URL goes to
the outputs map only (gitignored); the raw token is never committed.

THIS REPOSITORY IS PUBLIC. Every organisation, product, manufacturer and
price below is invented. No real supplier, partner or funder is named.

Two phases:

1. **Reset** (default; `--no-reset` skips it). Every take of this narrative
   creates an award, an approval, an order, a payment and a receipt, and a
   second take must start from the same world as the first. There is no MCP
   tool that purges a programme -- deliberately, since a purge is destructive
   -- so the reset runs `SupplyDataAccess.purge()` inside the deployed web
   task over ECS Exec (AWS profile `labs`). `purge()` refuses anything but a
   registered labs-only programme, so this cannot touch real data. The
   programme's update links go with it: they name contracts that no longer
   exist.

2. **Seed**, through the connect_labs MCP only -- the same operation registry,
   schemas and provenance stamping as the web UI. No private write path.

Prints (and writes to `--outputs`) a flat JSON map for the recorder's
`${var}` substitution.

    python scripts/walkthroughs/supply-test-kits/seed.py --outputs /tmp/x.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROGRAMME_ID = 10605
MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")
ROUND_LABEL = "Chlorine test kits — Q4 2026"

# ---------------------------------------------------------------------------
# A stdlib MCP-over-HTTP client. The recorder runs `setup:` under the canopy
# runtime's interpreter, so this script must not depend on anything the labs
# venv has and that one might not (httpx included).
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
                    "clientInfo": {"name": "supply-test-kits-seed", "version": "1"},
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
        """Call `supply_chain_<name>`, waiting out the MCP write-rate limit.

        The limit is per user, and the sibling supply narratives seed as the
        same user, so a burst from any of them can land on this one.
        """
        for attempt in range(8):
            try:
                return self._op(name, **arguments)
            except RuntimeError as error:
                if "rate limit" not in str(error).lower() or attempt == 7:
                    raise
                print(f"  {name}: rate-limited, waiting", file=sys.stderr)
                time.sleep(15)

    def _op(self, name: str, **arguments):
        """Call `supply_chain_<name>` in this programme; raise on a refusal."""
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
            raise RuntimeError(f"{name} refused: {content}")
        if isinstance(content, dict) and set(content) == {"value"}:
            return content["value"]
        return content


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

PURGE = f"""
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.update_links.models import UpdateLink
access = SupplyDataAccess(access_token="walkthrough-reset", program_id={PROGRAMME_ID}, caller=SYSTEM)
links = UpdateLink.objects.filter(program_id={PROGRAMME_ID}).delete()[0]
print("PURGED", access.purge(), "links", links)
"""


def reset():
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
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout.strip()
    encoded = base64.b64encode(PURGE.encode()).decode()
    command = f"python manage.py shell -c \"exec(__import__('base64').b64decode('{encoded}').decode())\""
    # The session needs stdin held open until the command has finished, or it
    # closes before the output arrives.
    with subprocess.Popen(["sleep", "40"], stdout=subprocess.PIPE) as keepalive:
        out = subprocess.run(
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
            stdin=keepalive.stdout,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        keepalive.kill()
    line = next((ln for ln in out.stdout.splitlines() if "PURGED" in ln), None)
    if line is None:
        sys.exit(f"reset failed:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}")
    print(f"reset: {line.strip()}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def seed(mcp: Mcp) -> dict:
    # Organisations are labs-wide and shared with the sibling supply narratives,
    # so these are upserts by slug -- never deletes.
    harmattan = mcp.op(
        "org_upsert",
        data={
            "slug": "harmattan-health-supplies",
            "name": "Harmattan Health Supplies",
            "country": "NG",
            "notes": "Invented in-country distributor for the supply walkthroughs.",
        },
    )
    aqualytic = mcp.op(
        "org_upsert",
        data={
            "slug": "aqualytic",
            "name": "Aqualytic",
            "country": "NG",
            "notes": "Invented water-quality technical partner for the supply walkthroughs.",
        },
    )
    programme_org = next(o for o in mcp.op("org_list") if o["slug"] == "dimagi")

    # The reagent and the comparator are products in their own right: a kit's
    # components have to name products in this catalogue.
    mcp.op(
        "commodity_upsert",
        data={
            "slug": "dpd1-reagent",
            "name": "DPD No. 1 reagent tablet",
            "category": "diagnostic",
            "base_unit": "tablet",
        },
    )
    mcp.op(
        "commodity_upsert",
        data={
            "slug": "colour-comparator",
            "name": "Chlorine colour comparator",
            "category": "equipment",
            "base_unit": "unit",
        },
    )
    # Aqualytic's specification, captured on the product. The figures are the
    # ones a free-chlorine check at a community dispenser turns on: the kit has
    # to detect the 0.2 mg/L residual and still read at the 2 mg/L dose.
    mcp.op(
        "commodity_upsert",
        data={
            "slug": "chlorine-test-kit",
            "name": "Free chlorine test kit",
            "category": "diagnostic",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "spec_reference": "Aqualytic test-kit specification AQ-TK-1 (2026)",
            "spec_requirements": [
                {
                    "field": "tests_per_kit",
                    "operator": ">=",
                    "value": 50,
                    "unit": "tests",
                    "rationale": "reagents for at least 50 tests in every kit",
                },
                {
                    "field": "range_min_mg_per_l",
                    "operator": "<=",
                    "value": 0.2,
                    "unit": "mg/L",
                    "rationale": "must detect the 0.2 mg/L minimum residual",
                },
                # The third -- range_max_mg_per_l >= 2.0, "must still read at
                # the 2 mg/L dispenser dose" -- is typed into the product form
                # ON CAMERA in scene 1, so it is deliberately not seeded.
            ],
        },
    )
    components = [
        {"commodity_slug": "dpd1-reagent", "quantity": "50", "base_unit": "tablet"},
        {"commodity_slug": "colour-comparator", "quantity": "1", "base_unit": "unit"},
    ]
    lumen = mcp.op(
        "item_upsert",
        data={
            "sku": "lumen-fc50",
            "name": "Lumen FC-50 free chlorine kit",
            "commodity_slug": "chlorine-test-kit",
            "manufacturer": "Lumen Diagnostics",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "shelf_life_months": 24,
            "status": "active",
            "components": components,
            # What one KIT holds: 50 tablets for 50 tests.
            "components_per": "pack",
            "spec_attributes": {"tests_per_kit": 50, "range_min_mg_per_l": 0.1, "range_max_mg_per_l": 3.5},
        },
    )
    brightwell = mcp.op(
        "item_upsert",
        data={
            "sku": "brightwell-pc50",
            "name": "Brightwell PoolCheck-50 kit",
            "commodity_slug": "chlorine-test-kit",
            "manufacturer": "Brightwell Labs",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "shelf_life_months": 24,
            "status": "active",
            "components": components,
            # What one KIT holds: 50 tablets for 50 tests.
            "components_per": "pack",
            "spec_attributes": {"tests_per_kit": 50, "range_min_mg_per_l": 0.1, "range_max_mg_per_l": 1.5},
        },
    )

    supplier = mcp.op(
        "supplier_create",
        data={
            "name": "Harmattan Health Supplies",
            "type": "distributor",
            "country": "NG",
            "city": "Kano",
            "status": "quoting",
            "org_id": harmattan["id"],
            "contacts": [{"name": "Sales desk", "role": "sales", "email": "sales@harmattan-health.example"}],
        },
    )
    warehouse = mcp.op(
        "supply_point_upsert",
        data={
            "slug": "harmattan-kano-warehouse",
            "name": "Harmattan warehouse, Kano",
            "kind": "central_store",
            "managed_by_org_id": harmattan["id"],
            "admin_area": "Kano",
            "source": "we_recorded",
        },
    )

    round_ = mcp.op(
        "tender_create",
        data={
            "label": ROUND_LABEL,
            "lines": [{"commodity_slug": "chlorine-test-kit", "quantity": "20", "quantity_unit": "kit"}],
            "delivery_point": {
                "name": "Harmattan warehouse, Kano",
                "city": "Kano",
                "country": "NG",
                "country_name": "Nigeria",
                "incoterm_requested": "DAP",
            },
            "response_deadline": "2026-10-09",
            "notes_to_supplier": "Kits to Aqualytic specification AQ-TK-1. Quote per kit, delivered Kano.",
        },
    )
    mcp.op("tender_open", tender_id=round_["id"])

    # Harmattan's two offers, as its sales desk sent them: per kit, delivered
    # to its own Kano warehouse, duties in. The cheaper kit is the one that
    # does not meet the specification -- that is the decision the scene turns on.
    def quote(item, amount):
        return mcp.op(
            "quote_record",
            data={
                "tender_id": round_["id"],
                "supplier_id": supplier["id"],
                "item_id": item["id"],
                "commodity_slug": "chlorine-test-kit",
                "as_quoted_amount": amount,
                "as_quoted_unit": "per_pack",
                "as_quoted_currency": "USD",
                "quantity_basis": "20",
                "quantity_basis_unit": "kit",
                "pack_spec_source": "trade_item_confirmed",
                "base_per_pack_stated": 50,
                "freight_basis": "included",
                "duties_basis": "included",
                "shelf_life_months_stated": 24,
                "lead_time_days": 21,
                "incoterm": "DAP",
                "received_on": "2026-09-18",
            },
        )

    quote_lumen = quote(lumen, "38.00")
    quote_brightwell = quote(brightwell, "29.50")

    # Harmattan's standing link: everything involving Harmattan in this
    # programme, now and later -- so the order placed on camera is on it.
    link = mcp.op(
        "update_link_issue",
        data={
            "org_id": harmattan["id"],
            "coverage": "organisation",
            "label": "Harmattan — everything it supplies and stores",
            "expires_in_days": 30,
        },
    )
    update_link = "/supply/u/" + link["token"] + "/"

    return {
        "program_id": PROGRAMME_ID,
        "tender_id": round_["id"],
        "quote_lumen_id": quote_lumen["id"],
        "quote_brightwell_id": quote_brightwell["id"],
        "item_lumen_id": lumen["id"],
        "item_brightwell_id": brightwell["id"],
        "supplier_id": supplier["id"],
        "warehouse_id": warehouse["id"],
        "harmattan_org_id": harmattan["id"],
        "aqualytic_org_id": aqualytic["id"],
        "programme_org_id": programme_org["id"],
        "update_link": update_link,
        # The day the take is filmed. The award is decided today, so every
        # date typed after it on camera must be today too, not a fixed day
        # that falls before it on the next day's take.
        "today": time.strftime("%Y-%m-%d"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-reset", action="store_true", help="Seed on top of whatever is there.")
    parser.add_argument("--outputs", help="Write the substitution map here as well as to stdout.")
    args = parser.parse_args()

    if not args.no_reset:
        reset()
    started = time.monotonic()
    outputs = seed(Mcp())
    print(f"seeded in {time.monotonic() - started:.1f}s", file=sys.stderr)
    text = json.dumps(outputs, indent=2)
    if args.outputs:
        Path(args.outputs).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
