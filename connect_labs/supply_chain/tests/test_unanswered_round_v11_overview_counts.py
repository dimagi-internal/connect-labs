"""Unanswered round v11: the overview's "N awaiting a reply" counts what its table lists.

THIS REPOSITORY IS PUBLIC. Every company here is invented.

The funnel counted every unanswered invitation ever sent -- closed rounds,
draft ones -- while the "No reply" lines beside it name only the suppliers
silent on a round still being chased. The two now share one rule, and the
funnel says which: "on open rounds".
"""

import datetime

import pytest

from connect_labs.supply_chain.standing import awaiting_reply, standing_rows
from connect_labs.supply_chain.templatetags.supply_chain_extras import source_stages
from connect_labs.supply_chain.tests import test_sophie_batch5 as batch5
from connect_labs.supply_chain.tests.test_history_timeline import AUG_3, PROGRAM, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _home, _open, _outreach
from connect_labs.supply_chain.tests.test_sophie_batch5 import _supplier

registered_synthetic = batch5.registered_synthetic
da = batch5.da
sophie = batch5.sophie
ace = batch5.ace
base = batch5.base
client_in_program = batch5.client_in_program
home_client = batch5.home_client


def _listed_silent(today=datetime.date(2026, 9, 12)) -> int:
    """How many suppliers the overview's table names under "No reply", across its tender rows."""
    count = 0
    for row in standing_rows(PROGRAM, today):
        if row.kind != "tender":
            continue
        lines = row.waiting_lines or (row.waiting_on,)
        for line in lines:
            if getattr(line, "heading", "") == "No reply":
                count += len(line.lines)
    return count


@pytest.mark.django_db
class TestTheAwaitingCountIsTheTablesCount:
    def test_closed_and_draft_rounds_are_not_counted(self, da, base):
        live = base["tender"]["id"]
        sahel = _supplier(da, "Sahel Nutrition")
        lagoon = _supplier(da, "Lagoon Foods")
        _open(da, live)
        _outreach(da, live, base["supplier"]["id"], "2026-09-01")
        _outreach(da, live, sahel["id"], "2026-09-01")
        _outreach(da, live, lagoon["id"], "2026-09-01")
        replied = [o for o in op(da, "outreach_list", AUG_3) if o["supplier_id"] == lagoon["id"]][0]
        op(da, "outreach_update", AUG_3, outreach_id=replied["id"], data={"responded": True})

        # A round opened, asked and closed: its silent supplier is history, not waiting.
        old = op(
            da,
            "tender_create",
            AUG_3,
            data={
                "label": "Old round",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
            },
        )
        _open(da, old["id"])
        _outreach(da, old["id"], sahel["id"], "2026-08-01")
        op(da, "tender_close", AUG_3, tender_id=old["id"])

        assert _listed_silent() == 2
        assert awaiting_reply(PROGRAM) == {"suppliers": 2, "tenders": 1}
        summary = op(da, "chain_summary", AUG_3, commodity_slug="rutf")["source"]
        assert summary["rfq_issued"]["invitations"] == 4
        assert summary["rfq_issued"]["awaiting_reply"] == _listed_silent()

        rfq = next(cell for cell in source_stages(summary) if cell.get("label") == "RFQ issued")
        assert "2 awaiting a reply on 1 open round" in str(rfq)

    def test_the_page_says_what_it_counted(self, da, base, home_client):
        _open(da, base["tender"]["id"])
        _outreach(da, base["tender"]["id"], base["supplier"]["id"], "2026-09-01")
        body = _home(home_client)
        assert "1 awaiting a reply on 1 open round" in body
