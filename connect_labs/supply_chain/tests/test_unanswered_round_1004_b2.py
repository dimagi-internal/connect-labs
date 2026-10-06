"""The unanswered tender, DDD 2026-10-04 batch 2: a Suppliers table that fits, a history that dates once.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

Builds on test_tracking_reality's world: one open RUTF tender, Kanem invited
on 6 Jul, Northgate a second supplier.
"""

import datetime
import re

import pytest
from django.utils import timezone
from django.utils.formats import date_format

from connect_labs.supply_chain.history.timeline import Entry, _drop_own_day
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import WHEN, _kanem_quote, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page

da = reality.da
world = reality.world
client_in_program = reality.client_in_program

REPLY_REF = "<reply-b2@mail.kanem.example.invalid>"


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _reply_today(da, world):
    """A reply recorded the day it came in (the world's own recording day)."""
    source = {"ref": REPLY_REF, "excerpt": "Our price is USD 54.50 a carton, CPT Kano.", "sender": "Grace"}
    op(
        da,
        "outreach_update",
        source=source,
        outreach_id=world["outreach"]["id"],
        data={
            "responded": True,
            "response_kind": "quote",
            "responded_on": timezone.localtime(WHEN).date().isoformat(),
        },
    )
    op(da, "quote_record", source=source, data=_kanem_quote(world))


# ---- A. the Suppliers table fits its card


@pytest.mark.django_db
class TestTheSuppliersTableFits:
    def test_a_sheet_of_one_value_columns_across_the_page(self, da, world, client_in_program):
        """Superseded the five wrapping columns beside a side rail (2026-10-05): the suppliers read as a
        sheet across the page, one stored value to a column, so each can be edited where it sits."""
        body = _tender_page(client_in_program, world["tender"]["id"])
        table = re.search(r'<table [^>]*data-testid="supplier-table".*?</table>', body, re.S).group(0)
        head = _text(re.search(r"<thead.*?</thead>", table, re.S).group(0))
        for column in ("Supplier", "State", "Asked", "Replied", "Last chased", "Price", "Delivery term", "Pack"):
            assert column in head
        assert 'class="sheet"' in table
        # Nothing beside it: the moves sit above the sheet, not in a rail that squeezes it.
        assert body.index('id="tender-moves"') < body.index('id="outreach"')

    def test_a_reply_day_has_its_own_editable_cell(self, da, world, client_in_program):
        _reply_today(da, world)
        body = _tender_page(client_in_program, world["tender"]["id"])
        outreach_id = world["outreach"]["id"]
        row = re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)
        cell = re.search(r'<td [^>]*data-testid="replied-on"[^>]*>.*?</td>', row, re.S).group(0)
        assert f'data-edit="outreach:{outreach_id}:responded_on"' in cell
        assert 'data-testid="replied"' in cell

    def test_what_a_quote_lacks_sits_under_the_quote(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        for quote_cell in re.findall(r'<td [^>]*data-testid="supplier-quote">.*?</td>', body, re.S):
            if 'data-testid="supplier-missing"' in quote_cell:
                assert quote_cell.index("supplier-missing") > 0

    def test_a_row_a_link_lands_on_clears_the_bar_and_the_header_row(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert re.search(r"\.supply-page tr\[id\] \{ scroll-margin-top: 7\.5rem; \}", body)
        assert f'id="outreach-{world["outreach"]["id"]}"' in body


# ---- B. the history says each date once


def test_a_reply_recorded_the_day_it_came_in_does_not_repeat_its_day():
    when = timezone.make_aware(datetime.datetime(2026, 10, 4, 10, 0))
    day = date_format(timezone.localtime(when), "j M Y")
    entry = Entry(
        when=when,
        sentence=f"Replied with a quote on {day}",
        actor="ACE (agent)",
        is_ai=True,
        excerpt="",
        source_ref="",
        entity="Outreach",
        identity="Sahel Co",
        what=f"Replied with a quote on {day}",
    )
    _drop_own_day(entry)
    assert entry.what == "Replied with a quote" and entry.sentence == "Replied with a quote"
    later = Entry(
        when=when,
        sentence="Replied with a quote on 1 Oct 2026",
        actor="Sophie",
        is_ai=False,
        excerpt="",
        source_ref="",
        what="Replied with a quote on 1 Oct 2026",
    )
    _drop_own_day(later)
    # Recorded on a later day, the day it came in is the line's to say.
    assert later.what == "Replied with a quote on 1 Oct 2026"


@pytest.mark.django_db
class TestTheEmailEventHead:
    def _event(self, client, world):
        timeline = _tender_page(client, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        return re.search(r'<li data-testid="email-event".*?</ol>\s*</li>', timeline, re.S).group(0)

    def test_one_head_line_and_no_preamble(self, da, world, client_in_program):
        _reply_today(da, world)
        event = self._event(client_in_program, world)
        head = event.split("<details", 1)[0]
        text = _text(head)
        assert "Email forwarded" not in text and "recorded by" not in text and "Email from" not in text
        assert 'data-testid="email-event-head"' in head and 'data-testid="actor-badge"' in head
        # No date in the head: the day heading above it carries it.
        assert not re.search(r"\d{1,2} [A-Z][a-z]{2} \d{4}", text)

    def test_the_reply_line_does_not_say_its_day_again(self, da, world, client_in_program):
        _reply_today(da, world)
        event = self._event(client_in_program, world)
        lines = [_text(li) for li in re.findall(r'<li data-testid="revision-line".*?</li>', event, re.S)]
        assert any("Replied with a quote" in line for line in lines)
        assert not any(re.search(r"Replied with a quote on ", line) for line in lines)

    def test_the_mail_domain_opens_with_the_email(self, da, world, client_in_program):
        _reply_today(da, world)
        event = self._event(client_in_program, world)
        details = re.search(r"<details.*?</details>", event, re.S).group(0)
        assert 'data-testid="email-arrival"' in details and "mail.kanem.example.invalid" in details

    def test_in_a_fold_the_history_is_not_a_box_inside_the_box(self, da, world, client_in_program):
        _reply_today(da, world)
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        wrapper = timeline.split("<ol data-timeline-list", 1)[0]
        assert "border-gray-200 rounded-lg" not in wrapper.rsplit("<div", 1)[1]
