"""Resolving a Connect org slug to the delivery partner behind it.

Every case below is a real slug from labs prod, because the failure mode is a
plausible-looking wrong name and only real data exercises the ways a slug and a
name diverge: truncation at 41 characters, workspace suffixes, French NGO
prefixes, apostrophes, plurals, and outright typos in the master list.
"""

from __future__ import annotations

import pytest

from connect_labs.pulse.partner_names import HIGH_CONFIDENCE, resolve


class TestHighConfidenceMatches:
    def test_exact_short_name(self):
        """`isodaf` is the master list's short name verbatim."""
        got = resolve("isodaf")
        assert got["parent"] == "Initiative For Social Development In Africa"
        assert got["tier"] in HIGH_CONFIDENCE

    def test_workspace_suffix(self):
        """A partner's second workspace: `isodaf-kogi-1` is still ISODAF."""
        assert resolve("isodaf-kogi-1")["parent"] == "Initiative For Social Development In Africa"

    def test_parenthetical_acronym(self):
        """The master name is "Centre for Well-being … (C-WINS)"; people say C-WINS."""
        assert "C-WINS" in resolve("c-wins")["parent"]

    def test_truncated_slug(self):
        """Connect truncates org slugs around 41 characters, so the slug is a
        prefix of the real name rather than equal to it."""
        got = resolve("zenith-of-the-girl-child-and-women-initiat")
        assert got["parent"].startswith("Zenith Of The Girl Child")
        assert got["tier"] in HIGH_CONFIDENCE

    def test_plural_difference(self):
        """`preterm-infants-…` against master "Preterm Infant Parents Network Uganda"."""
        assert resolve("preterm-infants-parents-network-uganda")["parent"] == ("Preterm Infant Parents Network Uganda")

    def test_french_ngo_prefix(self):
        """Connect carries an `ong-` prefix the master list does not."""
        assert resolve("ong-agir-pour-sauver-la-vie")["parent"] == "Agir Pour Sauver La Vie"

    def test_apostrophe_collapsed_not_split(self):
        """Connect slugifies "D'Entraide" to `dentraide`; splitting on the
        apostrophe instead yields `d-entraide` and the two never meet."""
        assert resolve("comite-dentraide-familiale-cef-asbl")["parent"] == "Comité D'Entraide Familiale"

    def test_short_acronym_suffix(self):
        """Short names are often 3-4 characters — `c3hd-ng-primary` is C3HD's."""
        assert resolve("c3hd-ng-primary")["parent"] == "Center for Child Care and Human Development"


class TestConnectNameWins:
    """Connect's own name is a real name, and matches the master list far better
    than a slug does. It is what links this workspace to Solina at all."""

    def test_connect_name_resolves_a_slug_that_alone_would_not(self):
        got = resolve("connect-nigeria", connect_name="Solina ECD Nigeria")
        assert got["parent"] == "Solina Health"
        assert "via connect name" in got["why"]

    def test_the_same_partner_is_reached_from_its_other_workspace(self):
        """The point of the mapping: `solina` and `connect-nigeria` are one
        partner running two Connect workspaces, 403,755 services between them,
        which the display previously showed as two unrelated organisations."""
        assert resolve("solina")["parent"] == "Solina Health"
        assert resolve("connect-nigeria", connect_name="Solina ECD Nigeria")["parent"] == "Solina Health"

    def test_a_person_looking_slug_is_a_real_partner_when_connect_names_it(self):
        """`edallariva` is Pachi Malawi. Guessing from the slug alone would have
        filed a real partner as somebody's personal workspace."""
        assert resolve("edallariva", connect_name="Pachi Malawi")["parent"] == (
            "Parent and Child Health Initiative (PACHI)"
        )


class TestRefusesToGuess:
    """A wrong parent name is worse than a visible slug."""

    def test_a_near_miss_is_returned_for_review_not_displayed(self):
        """The master list has a typo — "AREWA HEALTH TRUST INIATIVE". The close
        match is offered to a human and must not surface as the partner."""
        got = resolve("arewa-health-trust-initiative")
        assert got["parent"] == ""
        assert got["review"] is not None
        assert "AREWA" in got["review"]["candidate"]

    def test_an_unknown_slug_yields_nothing(self):
        got = resolve("ehealth-africa-connect-interviews")
        assert got["parent"] == ""
        assert got["tier"] in {"none", "subset", "fuzzy"}

    def test_never_deslugifies_into_a_title_cased_guess(self):
        """Mechanical title-casing reads plausibly and is wrong exactly where it
        matters: "C-WINS DGw" becomes "C Wins Dgw"."""
        got = resolve("some-partner-we-have-never-heard-of")
        assert got["parent"] == ""
        assert "Some Partner" not in got["parent"]

    def test_internal_workspaces_are_classified_not_matched(self):
        for slug in ("dimagi-chc-master-test", "ccc-delivery-sandbox", "ai-demo-space"):
            got = resolve(slug)
            assert got["parent"] == ""
            assert got["tier"] == "not-an-llo", slug

    def test_a_blank_slug_is_handled(self):
        assert resolve("")["parent"] == ""


class TestCoverage:
    """The mapping has to be worth having: it exists because 92% of delivery had
    no name at all."""

    def test_the_master_snapshot_is_present_and_populated(self):
        import json

        from connect_labs.pulse.partner_names import DATA

        raw = json.loads(DATA.read_text())
        assert len(raw["organizations"]) > 150
        assert raw["_provenance"]["source"].startswith("Google Sheet")

    @pytest.mark.parametrize(
        "slug,expected",
        [
            ("solina", "Solina Health"),
            ("eha-clinics-reach", "EHA Clinics (REACH Program)"),
            ("janna-health-foundation", "Janna Health Foundation"),
            ("rural-women-and-youth-development", "Rural Women And Youth Development"),
            ("roadmap-for-women-and-youth-development", "Roadmap for Women and Youth Development"),
            ("henike", "HENIKE Consultants Limited"),
        ],
    )
    def test_the_largest_partners_resolve(self, slug, expected):
        assert resolve(slug)["parent"] == expected
