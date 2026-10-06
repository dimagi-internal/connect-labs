"""A buyer's correction in a sheet leaves its mark on the History (DDD sheets batch 3).

THIS REPOSITORY IS PUBLIC. Every id, name, address and figure below is invented.
"""

import datetime
import json
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain import cells
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.history.timeline import quote_lineages, timeline_for_tender
from connect_labs.supply_chain.tests import test_history_timeline as timeline_tests
from connect_labs.supply_chain.tests.test_history_timeline import PROGRAM, _at, op

# The timeline tests' fixtures, shared rather than copied.
registered_synthetic = timeline_tests.registered_synthetic
da = timeline_tests.da
sophie = timeline_tests.sophie
ace = timeline_tests.ace
base = timeline_tests.base
client_in_program = timeline_tests.client_in_program

OCT_3, OCT_6 = _at(10, 3), _at(10, 6)
REPLY = "Dear Sophie, please find our offer: USD 42.50 per carton. Sent 5 Oct. Northwind sales"


def _quoted_reply(da, base, ace, *, sent_on="2026-10-05"):
    """The AI recorded an emailed reply and its quote, with the day Sophie forwarded it as Received."""
    outreach = op(
        da,
        "outreach_log",
        OCT_3,
        data={"tender_id": base["tender"]["id"], "supplier_id": base["supplier"]["id"], "sent_on": "2026-10-03"},
    )
    source = {"ref": "<offer-1@northwind.example>", "excerpt": REPLY}
    if sent_on:
        source["sent_on"] = sent_on
    op(
        da,
        "outreach_update",
        OCT_6,
        channel="mcp",
        actor=ace,
        source=source,
        outreach_id=outreach["id"],
        data={"responded": True, "response_kind": "quote", "responded_on": "2026-10-06"},
    )
    quote = op(
        da,
        "quote_record",
        OCT_6,
        channel="mcp",
        actor=ace,
        source=source,
        data={
            "tender_id": base["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": base["supplier"]["id"],
            "as_quoted_amount": "42.50",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "carton",
            "received_on": "2026-10-06",
        },
    )
    return outreach, quote


def _edit(client, key, value, was=""):
    return client.post(
        reverse("supply_chain:cell_edit"),
        data=json.dumps({"cell": key, "value": value, "was": was}),
        content_type="application/json",
    )


@pytest.mark.django_db
class TestACellCorrectionIsOnTheHistory:
    def _correct_received(self, da, quote, sophie):
        # The cell's own write path (cells.apply, what /supply/cells/ runs), attributed to Sophie.
        with seed_overrides(PROGRAM, actor=sophie, channel="web", recorded_at=OCT_6 + datetime.timedelta(hours=2)):
            return cells.apply(da, "quote", quote["id"], "received_on", "5 Oct 2026", was="2026-10-06")

    def test_the_cell_endpoint_writes_the_correction(self, client_in_program, da, base, ace, monkeypatch):
        monkeypatch.setattr("connect_labs.supply_chain.cells._access", lambda request: da)
        _, quote = _quoted_reply(da, base, ace)
        response = _edit(client_in_program, f"quote:{quote['id']}:received_on", "5 Oct 2026", "2026-10-06")
        assert response.status_code == 200, response.content
        entries = timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)
        assert any("corrected: received on 5 Oct (was 6 Oct)" in e.line for e in entries)

    def test_the_tender_page_history_says_what_was_corrected_by_whom(self, client_in_program, da, base, ace, sophie):
        _, quote = _quoted_reply(da, base, ace)
        self._correct_received(da, quote, sophie)

        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        ).content.decode()
        history = body.split('data-testid="timeline"', 1)[1]
        line = re.search(r'<li data-testid="revision-line" data-fields="[^"]*received_on[^"]*".*?</li>', history, re.S)
        assert line, "no History line for the corrected Received day"
        text = re.sub(r"<[^>]+>", "", line.group(0))
        assert "corrected: received on 5 Oct (was 6 Oct)" in text
        assert "Sophie Bello" in text

    def test_the_correction_reason_names_the_change(self, client_in_program, da, base, ace, sophie):
        from connect_labs.supply_chain.models import Quote

        _, quote = _quoted_reply(da, base, ace)
        result = self._correct_received(da, quote, sophie)
        new_id = int(result["key"].split(":")[1])

        assert Quote._base_manager.get(pk=new_id).correction_reason == "received 6 Oct → 5 Oct"
        # Beside the version's own "received on 5 Oct (was 6 Oct)" on the Quotes sheet it is
        # not said a second time.
        (earlier,) = quote_lineages([new_id], program_id=PROGRAM)[new_id].earlier
        assert earlier["changes"] == "received on 5 Oct (was 6 Oct)"
        assert earlier["reason"] == ""

    def test_the_email_says_when_it_was_sent_beside_when_it_was_forwarded(self, client_in_program, da, base, ace):
        _quoted_reply(da, base, ace)
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        ).content.decode()
        event = re.search(r'<li data-testid="email-event".*?</ol>\s*</li>', body, re.S).group(0)
        dates = re.search(r'<span data-testid="email-dates".*?</span></span>', event, re.S).group(0)
        assert re.sub(r"<[^>]+>", "", dates) == "sent 5 Oct · forwarded 6 Oct"

    def test_an_email_recorded_the_day_it_was_sent_says_sent_only(self, da, base, ace):
        _quoted_reply(da, base, ace, sent_on="2026-10-06")
        heads = [e for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM) if e.excerpt]
        assert heads and all(e.sent_on == datetime.date(2026, 10, 6) for e in heads)
        assert all(e.forwarded_on is None for e in heads)

    def test_without_a_sent_day_the_email_names_no_dates(self, da, base, ace):
        _quoted_reply(da, base, ace, sent_on=None)
        heads = [e for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM) if e.excerpt]
        assert heads and all(e.sent_on is None and e.forwarded_on is None for e in heads)


@pytest.mark.django_db
class TestEveryRegionAnEditChangesIsLive:
    """cell_edit.js re-reads the page and swaps each [data-live] region by its id."""

    @staticmethod
    def _live_ids(body):
        return set(re.findall(r'<[^>]*\bid="([^"]+)"[^>]*\bdata-live\b', body))

    def test_the_tender_pages_history_is_live(self, client_in_program, da, base, ace):
        _quoted_reply(da, base, ace)
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        ).content.decode()
        assert "history-fold" in self._live_ids(body)

    def test_the_order_pages_head_to_do_and_history_are_live(self, client_in_program, da, base):
        contract = op(
            da,
            "contract_create",
            OCT_3,
            data={
                "supplier_id": base["supplier"]["id"],
                "commodity_slug": "rutf",
                "buyer_of_record": "programme_org",
                "buyer_org_id": base["us"]["id"],
                "reference": "PO-LIVE",
                "quantity": "600",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        live = self._live_ids(body)
        assert {"order-head", "order-moves", "history-fold"} <= live
        # The To do block sits inside its live region, so a re-read swaps its count.
        moves = body.split('id="order-moves"', 1)[1].split('id="money-fold"', 1)[0]
        assert 'data-testid="on-us-count"' in moves
