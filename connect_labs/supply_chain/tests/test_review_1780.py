"""Four findings CodeRabbit raised on the UI pass (#1780).

Grouped in one file because they share a cause worth naming: three of the
four are states the new screens made REACHABLE for the first time. Nothing
rendered `Document.external_url` before, no view read a quote by id, and no
template outside the ones I edited called `humanise`. The fourth -- zero
freight reading as "amount not given" -- is the domain's own
confident-zero-for-an-unknown confusion, inverted.
"""

from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10511


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


class TestExternalUrlScheme:
    """A stored `javascript:` URL became executable the moment a page linked it.

    `document_attach` took `external_url` as an unconstrained string and
    `Document.objects.create` does not run `URLField` validation -- that only
    happens through a form or `full_clean`. Harmless while nothing rendered
    it; the quote page is the first thing that ever did.
    """

    def _attach(self, da, url):
        supplier = op(da, "supplier_create", data={"name": "Northwind"})
        return op(
            da,
            "document_attach",
            data={
                "kind": "other",
                "title": "customs file",
                "supplier_id": supplier["id"],
                "external_url": url,
                "source": "partner_reported",
            },
        )

    def test_an_https_link_is_accepted(self, da):
        doc = self._attach(da, "https://example.test/customs/abc.pdf")
        assert doc["external_url"] == "https://example.test/customs/abc.pdf"

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_an_unsafe_scheme_is_refused_and_not_stored(self, da, url):
        with pytest.raises(ValueError, match="https"):
            self._attach(da, url)
        assert op(da, "document_list") == []

    def test_a_bare_host_is_refused_rather_than_guessed(self, da):
        """Prefixing https:// for the caller would invent a scheme the caller
        did not state, on a field whose whole purpose is to point somewhere
        specific."""
        with pytest.raises(ValueError, match="https"):
            self._attach(da, "example.test/customs/abc.pdf")


class TestZeroIsAnAmount:
    def test_free_freight_reads_as_zero_not_as_unknown(self):
        """`if amount` is false for 0, so freight quoted at zero rendered as
        "amount not given" -- an unknown. Free freight is a real fact with
        its own basis flag, and this domain exists to keep those apart."""
        from connect_labs.supply_chain.templatetags.supply_chain_extras import stated_rows

        rows = {
            r["label"]: r["value"] for r in stated_rows({"freight_basis": "included", "freight_amount": Decimal("0")})
        }
        assert "not given" not in rows["Freight"], rows["Freight"]
        assert "0" in rows["Freight"]

    def test_an_absent_amount_still_reads_as_unknown(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import stated_rows

        rows = {r["label"]: r["value"] for r in stated_rows({"freight_basis": "excluded", "freight_amount": None})}
        assert "not given" in rows["Freight"]


def test_no_enum_identifier_reaches_the_stated_column():
    """`not_stated` reached the quote page as an identifier. The same fix had
    already been applied to the round table and missed here, so this asserts
    the property rather than the two fields it currently applies to: nothing
    in the stated column may carry an underscore where a word belongs.
    """
    from connect_labs.supply_chain.templatetags.supply_chain_extras import stated_rows

    quote = {
        "as_quoted_amount": "0.46",
        "as_quoted_currency": "USD",
        "as_quoted_unit": "per_base_unit",
        "quantity_basis": "100000",
        "quantity_basis_unit": "sachet",
        "pack_spec_source": "not_stated",
        "freight_basis": "not_specified",
        "duties_basis": "not_specified",
        "fx_rate_to_usd": "1",
    }
    offenders = [
        (row["label"], row["value"])
        for row in stated_rows(quote)
        if isinstance(row["value"], str) and "_" in row["value"]
    ]
    assert offenders == [], offenders
