"""DDD 002 batch 2: one button treatment on the tender header, owed rows that do
not repeat the group's day, a reply said as one sentence, no "for the ranking"
claim on a quote that is not ranked, and the overview's Waiting on as one
owner-labelled list with flags that count rather than restate."""

import re
from decimal import Decimal
from types import SimpleNamespace

import pytest

from connect_labs.supply_chain.procurement.services.comparison import per_pack_note
from connect_labs.supply_chain.standing import Flag
from connect_labs.supply_chain.templatetags.supply_chain_extras import lead_in
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question
from connect_labs.supply_chain.views import owed_groups

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def test_an_unranked_quote_does_not_claim_it_fed_the_ranking():
    no_figure = {"usd_per_pack_normalized": SimpleNamespace(amount=None, reasons=("something else",))}
    assert per_pack_note(no_figure, "sachet", "carton") == ""
    fx = {"usd_per_pack_normalized": SimpleNamespace(amount=None, reasons=("Quote is in EUR and no exchange rate",))}
    assert per_pack_note(fx, "sachet", "carton") == "(per carton once an exchange rate is recorded)"
    known = {"usd_per_pack_normalized": SimpleNamespace(amount=Decimal("46.50"), currency="USD", reasons=())}
    assert per_pack_note(known, "sachet", "carton").startswith("= USD 46.50")


def test_an_owed_row_on_the_groups_day_says_how_long_it_has_been_open():
    rows = [
        {"owed_to_org_id": 1, "owed_to": "Northgate", "kind": "question", "raised_on": "2026-09-18", "open": True},
        {"owed_to_org_id": 1, "owed_to": "Northgate", "kind": "question", "raised_on": "2026-09-20", "open": True},
    ]
    group = owed_groups(rows)[0]
    assert group["summary"] == "asked 18 Sep 2026 · 2 questions"
    first, second = group["items"]
    assert first["same_day_as_group"] and first["open_days"] >= 0
    assert not second["same_day_as_group"]


def test_waiting_on_labels_read_alike():
    assert str(lead_in("us: import permit")) == "<strong>Us</strong>: import permit"
    flag = Flag("Missing facts: A (freight)", heading="Missing facts", lines=[("A", "freight")])
    assert flag.lines == (("A", "freight"),) and str(flag).startswith("Missing facts")


@pytest.mark.django_db
class TestTheTenderPage:
    def test_secondary_header_buttons_share_one_treatment(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        edit = re.search(r'<a href="[^"]*/edit/[^"]*"\s+class="([^"]*)">Edit</a>', body).group(1)
        assert "text-brand-indigo" in edit and "text-gray-700" not in edit

    def test_an_owed_row_does_not_repeat_the_groups_day(self, da, world, client_in_program):
        _question(da, world, "One warehouse, or several?")
        body = _tender_page(client_in_program, world["tender"]["id"])
        owed = body[body.index('data-testid="owed-group"') :]
        owed = owed[: owed.index("</section>")] if "</section>" in owed else owed
        row = re.search(r"One warehouse, or several\?(.*?)</div>", owed, re.S).group(1)
        assert "asked" not in row
        assert 'data-testid="owed-age"' in row and "open " in row


@pytest.mark.django_db
def test_a_reply_reads_as_one_sentence_with_its_day(da, world, client_in_program):
    op(
        da,
        "outreach_update",
        outreach_id=world["outreach"]["id"],
        data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
    )
    body = _tender_page(client_in_program, world["tender"]["id"])
    assert re.search(r"Replied with a quote on 9 Jul 20\d\d", body)
    assert "Responded on" not in body
