"""Questions and promises on a tender: open first, grouped by supplier, an answer drafted until sent.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import re

import pytest

from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import _answer_as_sophie, _question, _tender_page, _text, op
from connect_labs.supply_chain.views import owed_groups

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


class TestWhatWeOweReads:
    def test_open_first_answered_apart_and_the_heading_counts_the_open(self, da, world, web):
        answered = _question(da, world, "One warehouse, or several?")
        _question(da, world, "Who is the importer of record?")
        _answer_as_sophie(da, answered["id"], "One warehouse in Kano.")
        body = _tender_page(web, world["tender"]["id"])
        assert (
            _text(re.search(r'<summary id="owed".*?</summary>', body, re.S).group(0))
            == "Questions and promises 1 open"
        )
        open_list = re.search(
            r'<div [^>]*data-testid="owed">.*?</div>\s*</div>\s*(?=<details|<div class="mb)', body, re.S
        )
        assert "Who is the importer of record?" in open_list.group(0)
        assert "One warehouse, or several?" not in open_list.group(0)
        group = re.search(r'<details data-testid="owed-answered"[^>]*>.*?</details>', body, re.S).group(0)
        assert " open>" not in group.split(">", 1)[0] + ">"  # shut unless it holds the row just saved
        assert "Answered by Sophie · 2 Oct 2026: One warehouse in Kano." in _text(group)
        assert 'data-testid="owed-status"' in group

        arrived = _tender_page(web, world["tender"]["id"], f"?changed=commitment-{answered['id']}")
        opening = re.search(r'<details data-testid="owed-answered"[^>]*>', arrived).group(0)
        assert opening.endswith(" open>")

    def test_answering_is_in_the_tenders_history(self, da, world, web):
        asked = _question(da, world)
        _answer_as_sophie(da, asked["id"], "We are the importer.")
        history = _text(_tender_page(web, world["tender"]["id"]).split('data-testid="timeline"', 1)[1])
        assert "To do · Northgate Rehearsal Commodities · answered: We are the importer. Sophie" in history
        assert (
            "To do · Northgate Rehearsal Commodities · recorded: they asked: Who is the importer of record?" in history
        )


class TestWhatWeOweBySupplier:
    def _ask(self, da, world, text, raised):
        return op(
            da,
            "commitment_record",
            data={
                "kind": "question",
                "supplier_id": world["northgate"]["id"],
                "tender_id": world["tender"]["id"],
                "text": text,
                "raised_on": raised,
                "source": "supplier_reported",
            },
        )

    def test_one_header_per_supplier(self, da, world, client_in_program):
        for text, raised in (("Importer?", "2026-09-18"), ("Labels?", "2026-09-19"), ("Warehouse?", "2026-09-20")):
            self._ask(da, world, text, raised)
        body = _tender_page(client_in_program, world["tender"]["id"])
        heads = re.findall(r'data-testid="owed-group-head"[^>]*>(.*?)</div>', body, re.S)
        assert [_text(h) for h in heads] == ["Northgate Rehearsal Commodities · asked 18 Sep 2026 · 3 questions"]
        owed = body.split('data-testid="owed"', 1)[1].split('id="drafts"', 1)[0]
        assert owed.count("Northgate Rehearsal Commodities") == 1

    def test_the_just_answered_row_wears_one_pill(self, da, world, client_in_program):
        asked = self._ask(da, world, "Importer?", "2026-09-18")
        op(da, "commitment_resolve", channel="web", commitment_id=asked["id"], resolution="We are.")
        body = _tender_page(client_in_program, world["tender"]["id"], f"?changed=commitment-{asked['id']}")
        row = re.search(
            rf'<div [^>]*data-commitment-id="{asked["id"]}".*?data-testid="owed-answer"', body, re.S
        ).group(0)
        # One line since DDD 002 batch 1: "Answered by Sophie · 2 Oct 2026 (just now): We are."
        answer = re.search(
            r'data-testid="owed-answer".*?</div>', body[body.index(f'data-commitment-id="{asked["id"]}"') :], re.S
        ).group(0)
        # Since DDD v11 an answer written into a reply not yet sent reads as drafted.
        assert re.search(r'data-testid="owed-status"[^>]*>Answer drafted<', answer)
        assert "(just now): We are." in re.sub(r"<[^>]+>", "", answer)
        assert 'data-testid="changed-chip"' not in row


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


@pytest.mark.django_db
class TestAnOwedRow:

    def test_an_owed_row_does_not_repeat_the_groups_day(self, da, world, client_in_program):
        _question(da, world, "One warehouse, or several?")
        body = _tender_page(client_in_program, world["tender"]["id"])
        owed = body[body.index('data-testid="owed-group"') :]
        owed = owed[: owed.index("</section>")] if "</section>" in owed else owed
        row = re.search(r"One warehouse, or several\?(.*?)</div>", owed, re.S).group(1)
        assert "asked" not in row
        assert 'data-testid="owed-age"' in row and "open " in row


@pytest.mark.django_db
class TestTheDraftedAnswer:
    def test_an_answer_in_an_unsent_reply_reads_drafted_until_the_reply_is_sent(self, da, world, client_in_program):
        asked = _question(da, world)
        op(da, "commitment_resolve", channel="web", commitment_id=asked["id"], resolution="We are.")
        body = _tender_page(client_in_program, world["tender"]["id"])
        row = re.search(rf'data-commitment-id="{asked["id"]}".*?</div>', body, re.S).group(0)
        text = " ".join(re.sub(r"<[^>]+>", " ", row).split())
        assert "Answer drafted · goes out with your reply to Northgate Rehearsal Commodities" in text
        assert "Answered" not in text
        op(da, "commitment_reply_sent", channel="web", commitment_ids=[asked["id"]])
        body = _tender_page(client_in_program, world["tender"]["id"])
        row = re.search(rf'data-commitment-id="{asked["id"]}".*?</div>', body, re.S).group(0)
        assert "Answered" in row and "Answer drafted" not in row

    def test_marking_an_open_question_sent_is_refused(self, da, world):
        asked = _question(da, world)
        with pytest.raises(ValueError):
            op(da, "commitment_reply_sent", commitment_ids=[asked["id"]])
