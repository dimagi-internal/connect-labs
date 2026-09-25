"""A store owed goods with no promised date says so, rather than trailing off.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

"expected: 400 jerry cans from A water donor · not counted as cover" reads as
though the arrival were merely unremarkable. The truth it is hiding is that
nobody knows when -- which for a blocked donation is the most important fact
on the page, and the first thing a funder asks about.

`_expected_inbound` (stock/services/network.py) leaves `expected_on` as None
unless the contract carries BOTH `signed_on` and `promised_lead_time_days`,
and that is correct: it refuses to invent a date it was never given. The
defect was downstream, in the template, which hid the whole clause behind
`{% if expected.expected_on %}` and so said nothing at all.

The two cases are tested together on purpose. A page that says "arrival date
not known" about everything would pass a no-date test on its own while
destroying the case that DOES have a date, which is the one a reader uses to
tell late from unknowable.
"""

import re

import pytest

from connect_labs.supply_chain.tests import test_stock_page_honesty as honesty

# Bound rather than imported. pytest resolves a fixture by its NAME in the
# module under collection, so these have to be module-level names here -- but
# `from ... import world` then reads to flake8 as an import shadowed by every
# test that takes `world` as a parameter (F811, seven times). Binding the same
# objects as attributes gives pytest exactly what it needs and gives flake8
# nothing to complain about.
da = honesty.da
world = honesty.world
scoped = honesty.scoped
_late_donation = honesty._late_donation
_stock_page = honesty._stock_page
_text = honesty._text

pytestmark = pytest.mark.django_db


def _consignment_lines(html):
    """Just the `data-expected` rows, not the whole page.

    The stock page explains its own cover rule in prose that contains the word
    "overdue" ("below its minimum it is overdue"), about a supply point rather
    than about a consignment. Asserting over the whole page therefore reads
    that sentence as though it were this one -- which is how a test passes or
    fails for a reason it was not written about.
    """
    lines = re.findall(r"<div[^>]*data-expected[^>]*>(.*?)</div>", html, re.S)
    assert lines, "no consignment line on the page at all"
    return _text(" ".join(lines))


def _undated_donation(world):
    """Confirmed, owed, and carrying no promised lead time.

    Evidence Action's chlorine import: agreed and behind, with no date anybody
    can stand behind. The absence of `promised_lead_time_days` is the whole
    point -- do not add one to make the row tidier.
    """
    return world["contract"](
        reference="DON-3",
        status="confirmed",
        quantity="400",
        signed_on=None,
    )


class TestAnArrivalDateThatIsKnown:
    def test_a_late_consignment_still_names_the_day_it_was_due(self, scoped, world):
        _late_donation(world)
        line = _consignment_lines(_stock_page(scoped))
        assert "overdue since" in line, line
        # And it does NOT claim the date is unknown: it is known, and passed.
        assert "no arrival date" not in line.lower()


class TestAnArrivalDateThatIsNot:
    def test_a_consignment_with_no_promised_date_says_the_date_is_unknown(self, scoped, world):
        _undated_donation(world)
        line = _consignment_lines(_stock_page(scoped))
        assert "expected:" in line, "the scenario needs the consignment to be owed at all"
        assert "no arrival date has been given" in line.lower(), line

    def test_it_is_not_dressed_as_overdue(self, scoped, world):
        """Unknown is not late. A date nobody promised cannot have been missed.

        Scoped to the consignment line, and to its colour as well as its
        words: `overdue` is `text-orange-700` on this page, so borrowing that
        class would say "late" in the one channel a reader takes in first.
        """
        _undated_donation(world)
        html = _stock_page(scoped)
        line = _consignment_lines(html)
        assert "overdue" not in line.lower(), line
        rendered = " ".join(re.findall(r"<div[^>]*data-expected[^>]*>(.*?)</div>", html, re.S))
        assert "text-orange-700" not in rendered, rendered
