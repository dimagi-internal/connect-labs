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

import pytest

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services import tracker_import as t

PROGRAM = 10503


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


def _group_row(round_one="Round 1 Quote (500 cartons)", round_two="Feb Re-quote (2,000 cartons)"):
    """Row 4: the sheet's merged group headers, which NAME the rounds."""
    row = [""] * 20
    row[t.ROUNDS[0]["label_column"]] = round_one
    row[t.ROUNDS[1]["label_column"]] = round_two
    return row


def _row(*, price="$52.42", freight="Not specified", name="Harmattan Foods"):
    """A tracker row in the sheet's own 20-column shape."""
    row = [""] * 20
    row[t.NAME] = name
    row[t.TYPE] = "Manufacturer"
    row[t.LOCATION] = "Nigeria"
    spec = t.ROUNDS[0]
    row[spec["contacted"]] = "2026-05-01"
    row[spec["responded"]] = "Yes"
    row[spec["quote_date"]] = "2026-05-06"
    row[spec["price"]] = price
    row[spec["freight"]] = freight
    return row


class TestDryRun:
    """A dry run's job is to preview what the sheet will and will not give up.

    `refused` was a hardcoded empty list on that path, so previewing a sheet
    with five refusals reported none. An empty list is not an absence of
    information -- it ASSERTS that nothing was refused, which is exactly the
    substitution (a confident zero for an unknown) that the rest of this
    domain exists to refuse. And the advice attached to the operation is "use
    dry_run first", so the misleading half is the one a reader sees first.
    """

    @pytest.mark.django_db
    def test_a_dry_run_reports_the_same_refusals_the_real_run_makes(self, monkeypatch):
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM)

        dry = t.import_tracker(da, ensure_commodity=True, dry_run=True)
        real = t.import_tracker(da, ensure_commodity=True)

        assert dry["refused"], "a dry run reported nothing refused for a sheet that refuses something"
        assert dry["refused"] == real["refused"]
        assert any("freight recorded as not specified" in r for r in dry["refused"])

    @pytest.mark.django_db
    def test_a_dry_run_still_writes_nothing(self, monkeypatch):
        """The refusals now come from walking the same code, so the write is
        what has to be suppressed -- not the traversal."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM)

        t.import_tracker(da, ensure_commodity=True, dry_run=True)

        assert call_operation("supplier_list", da, {}) == []
        assert call_operation("round_list", da, {}) == []
        assert call_operation("commodity_list", da, {}) == []


class TestRoundLabels:
    """A round is named by the sheet, not by this module.

    The labels were literals: "Round 1 — May 2026" and "Round 2 — February
    re-quote". The sheet's own header for the first is "Round 1 Quote (500
    cartons)" -- no month anywhere. May was ONE supplier's quote date (DABS,
    18 May 2026) promoted into the round's identity, and Round 1 actually
    runs from EHA's 23 Feb quote to DABS's 18 May one. So the label was
    wrong for EHA and misleading for the rest, and because it lived in a
    string constant no derivation guard could catch it -- the same
    stored-derived-value substitution this domain refuses everywhere it can
    see one.
    """

    def test_a_round_is_named_by_the_sheets_own_group_header(self):
        labels = t._round_labels(_group_row())
        assert labels == ["Round 1 Quote (500 cartons)", "Feb Re-quote (2,000 cartons)"]

    def test_no_label_asserts_a_month_the_sheet_does_not_state(self):
        labels = t._round_labels(_group_row())
        assert not any("May" in label for label in labels)

    def test_two_rounds_resolving_to_one_name_is_refused_not_merged(self):
        """Reading the label off the sheet made a collision reachable for the
        first time: as literals the two were distinct by construction.

        `_ensure_rounds` keys on the label, so two identical headers map both
        specs to ONE round id and `_load_round` then writes the Feb re-quote's
        prices against Round 1 -- two rounds silently collapsed, with every
        quantity and age attributed to the wrong one. Refused rather than
        disambiguated: appending a suffix would invent a name, which is the
        defect this whole change exists to remove.
        """
        with pytest.raises(t.TrackerImportError) as caught:
            t._round_labels(_group_row(round_one="Quote", round_two="Quote"))
        assert "distinct" in str(caught.value)
        assert "Quote" in str(caught.value)

    def test_a_stated_header_colliding_with_the_other_fallback_is_refused(self):
        """The fallbacks are real labels too, so a header reading "Round 2"
        on the FIRST round collides with the second's fallback."""
        with pytest.raises(t.TrackerImportError):
            t._round_labels(_group_row(round_one="Round 2", round_two=""))

    def test_a_blank_header_falls_back_to_an_ordinal_never_an_inferred_month(self):
        labels = t._round_labels(_group_row(round_one="", round_two=""))
        assert labels == ["Round 1", "Round 2"]

    def test_the_header_rows_are_dropped_by_position_not_truthiness(self, monkeypatch):
        """Rows 4 and 5 both carry text in the supplier-name column
        ("Supplier Identification", "Supplier name"), so a truthiness filter
        would import two phantom suppliers."""
        group = _group_row()
        column_headers = [""] * 20
        column_headers[t.NAME] = "Supplier name"
        payload = {"values": [group, column_headers, _row(name="Harmattan Foods")]}

        class _Response:
            status_code = 200

            def json(self):
                return payload

            def raise_for_status(self):
                pass

        monkeypatch.setattr(t, "_load_credentials", lambda: _FakeCreds())
        monkeypatch.setattr(t.httpx, "get", lambda *a, **k: _Response())

        returned_group, rows = t._read_sheet("sheet-id")
        assert returned_group == group
        assert [r[t.NAME] for r in rows] == ["Harmattan Foods"]


class _FakeCreds:
    token = "unused"
    service_account_email = "sa@example.com"

    def refresh(self, _request):
        pass
