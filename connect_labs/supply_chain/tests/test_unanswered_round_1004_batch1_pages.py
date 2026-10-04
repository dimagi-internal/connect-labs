"""Unanswered round, 2026-10-04 batch 1: one Suppliers table on the tender page.

The Invitations table listed the same suppliers as the Suppliers table above it, in other
words ("No reply · 17 days" beside "Silent 17d") and with "Record a reply" on every row,
even for a supplier whose reply was recorded. It is folded into the Suppliers table: a
Replied column, the invitation control in its header, a small menu per row.

THIS REPOSITORY IS PUBLIC. Every supplier and person here is invented.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests.test_history_timeline import AUG_3, op

registered_synthetic = timeline.registered_synthetic
da = timeline.da
sophie = timeline.sophie
ace = timeline.ace
base = timeline.base
client_in_program = timeline.client_in_program


def _page(client, tender_id, query=""):
    return client.get(reverse("supply_chain:procurement_tender_detail", args=[tender_id]) + query).content.decode()


def _rows(body):
    rows = re.findall(r'<tr[^>]*data-testid="supplier-row".*?</tr>', body, re.S)
    return {re.search(r'data-supplier-id="(\d+)"', r).group(1): r for r in rows}


@pytest.fixture
def asked(da, base):
    tender_id = base["tender"]["id"]
    op(da, "tender_open", AUG_3, tender_id=tender_id)
    quiet = op(da, "supplier_create", AUG_3, data={"name": "Lagoon Foods"})
    for supplier in (base["supplier"], quiet):
        op(
            da,
            "outreach_log",
            AUG_3,
            data={"tender_id": tender_id, "supplier_id": supplier["id"], "sent_on": "2026-08-03"},
        )
    by_supplier = {o["supplier_id"]: o for o in op(da, "outreach_list", AUG_3, tender_id=tender_id)}
    replied = by_supplier[base["supplier"]["id"]]
    op(
        da,
        "outreach_update",
        AUG_3,
        outreach_id=replied["id"],
        data={"responded": True, "responded_on": "2026-08-10", "response_kind": "declined"},
    )
    return {"tender_id": tender_id, "replied": replied, "quiet": by_supplier[quiet["id"]]}


@pytest.mark.django_db
class TestOneSuppliersTable:
    def test_the_invitations_are_folded_into_the_suppliers_table(self, client_in_program, asked):
        body = _page(client_in_program, asked["tender_id"])
        assert 'data-testid="fold-invitations"' not in body
        assert body.count('data-testid="supplier-table"') == 1
        assert re.search(r'<th scope="col"[^>]*>Replied</th>', body)
        assert "+ Record an invitation" in body
        rows = _rows(body)
        assert len(rows) == 2
        for supplier_id, row in rows.items():
            assert reverse("supply_chain:supplier_detail", args=[int(supplier_id)]) in row

    def test_record_a_reply_is_offered_only_where_none_is_recorded(self, client_in_program, asked):
        rows = _rows(_page(client_in_program, asked["tender_id"]))
        answered = rows[str(asked["replied"]["supplier_id"])]
        silent = rows[str(asked["quiet"]["supplier_id"])]
        assert "Record a reply" not in answered and 'data-testid="row-edit"' in answered
        assert "Record a reply" in silent and 'data-testid="row-edit"' not in silent
        assert re.search(r'data-testid="replied">\s*10 Aug', answered)
        # One word for silence on the page: the state chip, never "No reply · N days".
        assert re.search(r'data-testid="supplier-state">Silent \d+d<', silent)
        assert "No reply" not in silent

    def test_marking_a_reminder_sent_returns_to_the_merged_row_marked(self, client_in_program, asked):
        outreach_id = asked["quiet"]["id"]
        response = client_in_program.post(
            reverse("supply_chain:procurement_outreach_chase", args=[outreach_id]),
            {"last_reminder_on": "2026-08-20"},
        )
        assert response.status_code == 302
        location = response["Location"]
        assert location.endswith(f"#outreach-{outreach_id}")
        assert f"changed=outreach-{outreach_id}" in location and "cell=last_chased" in location
        body = client_in_program.get(location.split("#")[0]).content.decode()
        (row,) = (r for r in _rows(body).values() if "data-changed" in r)
        assert f'id="outreach-{outreach_id}"' in row
        chased = re.search(r'data-testid="supplier-chased">(.*?)</td>', row, re.S).group(1)
        assert "1st reminder" in chased and "Reminder sent" in chased
