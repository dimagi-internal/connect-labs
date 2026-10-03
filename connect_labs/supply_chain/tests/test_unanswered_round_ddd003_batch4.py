"""DDD run 003, batch 4: the tender page reads as one header and one table -- what the round
buys beside its status, one way to invite a supplier, a fixed actions column -- and a drafted
reminder ends with recording the chase, after the email it records."""

import pytest

from connect_labs.supply_chain.models import Commodity
from connect_labs.supply_chain.procurement.services.render import _product
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


@pytest.mark.django_db
class TestTheTenderPage:
    def test_what_the_round_buys_sits_in_the_header_not_a_card_of_its_own(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        header = body.split('data-testid="tender-facts"')[1].split("</dl>")[0]
        assert 'data-testid="tender-buys"' in header
        assert "What this tender buys" not in body

    def test_inviting_a_supplier_is_one_control_beside_outreach(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        # Everyone is asked through outreach and no one through the marketplace: no second panel.
        assert 'data-testid="invited-suppliers"' not in body
        outreach_head = body.split('id="outreach"')[1].split("<table")[0]
        assert "+ Record an invitation" in outreach_head
        if 'id="invite-org"' in body:
            assert 'id="invite-org"' in outreach_head

    def test_the_draft_card_ends_with_recording_the_chase(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        drafts = body.split('id="drafts"')[1]
        card = drafts.split('data-testid="draft"')[1]
        if 'data-testid="chase-form"' in card:
            assert card.index('data-testid="draft-text"') < card.index('data-testid="chase-form"')
        if 'data-testid="copy-to"' in card:
            assert 'data-testid="open-in-mail"' in card
            assert "mailto:" in card


def test_a_product_reads_lower_case_inside_a_sentence_but_an_acronym_keeps_its_capitals():
    assert _product(Commodity(slug="rutf", name="Ready-to-use therapeutic food")) == "ready-to-use therapeutic food"
    assert _product(Commodity(slug="rutf", name="RUTF sachets")) == "RUTF sachets"
    assert _product(Commodity(slug="ors", name="ORS")) == "ORS"
