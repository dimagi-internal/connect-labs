"""Reading a procurement tracker written by a human.

The parsing rules are the unit worth testing: the network read is a thin
wrapper, but what counts as a price, what counts as a date, and above all
what gets REFUSED are decisions with consequences. The refusals are the
reason this importer exists rather than a copy-paste.

Every sample cell below is taken verbatim in shape from a real tracker, with
supplier names replaced. The real names are never written into this
repository -- that is the whole point of reading them from Drive at runtime.
"""

from decimal import Decimal

from connect_labs.supply_chain.procurement.services import tracker_import as t


class TestPrice:
    def test_a_clean_figure_is_priced_per_pack_the_column_unit(self):
        assert t._price("$52.42") == (Decimal("52.42"), "per_pack", None)
        assert t._price("$50") == (Decimal("50"), "per_pack", None)
        assert t._price("1,240.50") == (Decimal("1240.50"), "per_pack", None)

    def test_a_per_sachet_price_is_taken_at_the_unit_the_text_names(self):
        amount, unit, refusal = t._price("$0.46/sachet")
        assert (amount, unit, refusal) == (Decimal("0.46"), "per_base_unit", None)

    def test_a_hand_derived_figure_is_refused_and_the_stated_one_kept(self):
        """The load-bearing rule. This exact cell shape is why the importer
        exists: $0.46 is what a supplier said, and ~$69 is arithmetic its own
        author flagged as unconfirmed. Importing the $69 would turn a caveat
        into a stored number that looks authoritative."""
        cell = "$0.46/sachet (quoted). Carton price not given; ~$69/carton is derived " "@150/carton, unconfirmed."
        amount, unit, refusal = t._price(cell)
        assert amount == Decimal("0.46")
        assert unit == "per_base_unit"
        assert refusal is None

    def test_prose_with_no_figure_is_refused_and_quoted_back(self):
        amount, unit, refusal = t._price("Need to confirm - also duties/taxes due to IDEC")
        assert amount is None
        assert unit is None
        assert "no stated figure" in refusal
        assert "IDEC" in refusal, "the refusal should quote the cell, so it can be acted on"

    def test_the_blanks_a_human_writes_are_not_zero(self):
        for blank in ("", "  ", "-", "—", "N/A", "n/a", "None", "Pending", "TBC"):
            assert t._price(blank) == (None, None, None), blank


class TestDate:
    def test_it_reads_the_four_ways_the_tracker_writes_dates(self):
        assert t._date("20 April 2026") == "2026-04-20"
        assert t._date("9 Sep 2026") == "2026-09-09"
        assert t._date("2026-09-26") == "2026-09-26"
        # Month-first: the row this comes from was re-contacted 9 Sep and the
        # sheet itself is dated 12 Sep, so 9 October would be in the future.
        assert t._date("9/10/2026") == "2026-09-10"

    def test_an_ambiguous_slash_date_is_flagged_not_silently_resolved(self):
        """The sheet's locale is en_GB, which implies day-first, while its
        data implies month-first. The two disagree and a cell cannot settle
        it, so it is parsed AND reported rather than chosen quietly."""
        assert t.ambiguous_numeric_date("9/10/2026") is True
        # 26 cannot be a month, so there is nothing to be ambiguous about.
        assert t.ambiguous_numeric_date("9/26/2026") is False
        # Same either way round.
        assert t.ambiguous_numeric_date("9/9/2026") is False
        assert t.ambiguous_numeric_date("9 Sep 2026") is False

    def test_an_unreadable_date_is_none_never_today(self):
        """Defaulting to today would silently date an outreach to the day of
        the import, which is exactly the sort of plausible-looking wrong
        figure this domain refuses."""
        assert t._date("sometime in the spring") is None
        assert t._date("Pending") is None
        assert t._date("") is None


class TestCountry:
    def test_it_reads_a_country_out_of_free_text(self):
        assert t._country("Nigeria (Lagos)") == "NG"
        assert t._country("Burkina Faso (Ouagadougou)") == "BF"
        assert t._country("Ethiopia") == "ET"

    def test_an_unrecognised_location_is_blank_not_guessed(self):
        """A wrong country code silently changes freight and duty reasoning,
        so an unreadable one is left empty and reported."""
        assert t._country("somewhere in the Sahel") == ""
        assert t._country("") == ""


class TestContacts:
    def _row(self, names, emails, phones=""):
        row = [""] * 20
        row[t.CONTACT], row[t.EMAIL], row[t.PHONE] = names, emails, phones
        return row

    def test_names_and_addresses_are_paired_when_the_counts_match(self):
        contacts = t._contacts(self._row("Ada; Grace", "ada@example.test; grace@example.test"))
        assert contacts == [
            {"name": "Ada", "email": "ada@example.test", "role": "sales"},
            {"name": "Grace", "email": "grace@example.test", "role": "sales"},
        ]

    def test_mismatched_counts_are_kept_apart_rather_than_paired(self):
        """Four addresses against one name is common in this tracker, and
        zipping them would invent three people."""
        contacts = t._contacts(self._row("Ada", "a@example.test; b@example.test; c@example.test"))
        emails = [c.get("email") for c in contacts if c.get("email")]
        assert emails == ["a@example.test", "b@example.test", "c@example.test"]
        assert any(c.get("note") == "names not matched to addresses" for c in contacts)
        assert not any(c.get("name") == "Ada" and c.get("email") for c in contacts)

    def test_a_dash_is_not_a_contact(self):
        assert t._contacts(self._row("-", "-")) == []


class TestSupplierStatus:
    def _row(self, status="", responded_1="", responded_2=""):
        row = [""] * 20
        row[t.STATUS] = status
        row[7], row[13] = responded_1, responded_2
        return row

    def test_an_explicit_not_yet_contacted_wins_over_everything(self):
        assert t._supplier_status(self._row(status="Not yet contacted"), has_quote=False) == "identified"

    def test_a_quote_makes_a_supplier_quoting(self):
        assert t._supplier_status(self._row(), has_quote=True) == "quoting"

    def test_a_reply_without_a_quote_is_responsive(self):
        assert t._supplier_status(self._row(responded_1="Yes (initial)"), has_quote=False) == "responsive"

    def test_silence_is_contacted_not_responsive(self):
        assert t._supplier_status(self._row(), has_quote=False) == "contacted"


def test_the_operation_is_registered_as_a_write():
    """It was a management command only, which made it the one capability in
    this domain the API and MCP could not reach -- and left no way to run the
    import where there is no shell."""
    from connect_labs.supply_chain.operations import get_operation

    assert get_operation("tracker_import").is_write is True
