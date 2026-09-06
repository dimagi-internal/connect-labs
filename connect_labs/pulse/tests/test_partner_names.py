"""Resolving a Connect org slug to the delivery partner behind it.

Every partner here is invented. Earlier versions of this file asserted against
the real directory, which meant real organisations' names — and, for the
unmatched cases, real colleagues' usernames — lived in the repository to
demonstrate behaviour that synthetic data demonstrates just as well.

What is NOT invented is the shape of each case. Each fixture reproduces a way a
slug and a name actually diverge in production: truncation at 41 characters,
workspace suffixes, French NGO prefixes, apostrophes, plurals, dropped
connectives, and typos in the directory itself.
"""

from __future__ import annotations

import pytest

from connect_labs.pulse.models import PulsePartner, PulsePartnerAlias
from connect_labs.pulse.partner_names import HIGH_CONFIDENCE, invalidate, resolve

pytestmark = pytest.mark.django_db


PARTNERS = [
    ("Foreland Rural Health Trust", "FRHT"),
    ("Centre for Riverine Nutrition (CRIN)", "CRIN"),
    ("Northbank Association Of Midwives And Community Birth Attendants", "NAMCBA"),
    ("Preterm Infant Parents Circle", "PIPC"),
    ("Agir Pour La Sante Rurale", "APSR"),
    ("Comite D'Entraide Rurale", "CER"),
    ("Peace And Rural Development Initiative", "PRDI"),
    ("Silverbrook Health Partners", "Silverbrook"),
]


@pytest.fixture(autouse=True)
def directory():
    """The directory as this module sees it: a table, loaded from the sheet."""
    for name, short in PARTNERS:
        PulsePartner.objects.create(name=name, short=short)
    invalidate()
    yield
    invalidate()


class TestHighConfidenceMatches:
    def test_exact_short_name(self):
        """The slug is the directory's short name verbatim."""
        got = resolve("frht")
        assert got["parent"] == "Foreland Rural Health Trust"
        assert got["tier"] in HIGH_CONFIDENCE

    def test_workspace_suffix(self):
        """A partner's second workspace is still that partner."""
        assert resolve("frht-kano-2")["parent"] == "Foreland Rural Health Trust"

    def test_parenthetical_acronym(self):
        """The name carries "(CRIN)"; people refer to it by that."""
        assert resolve("crin")["parent"] == "Centre for Riverine Nutrition (CRIN)"

    def test_truncated_slug(self):
        """Connect truncates org slugs around 41 characters, so the slug is a
        prefix of the real name rather than equal to it."""
        got = resolve("northbank-association-of-midwives-and-com")
        assert got["parent"].startswith("Northbank Association")
        assert got["tier"] in HIGH_CONFIDENCE

    def test_plural_difference(self):
        """`preterm-infants-…` against a directory name in the singular."""
        assert resolve("preterm-infants-parents-circle")["parent"] == "Preterm Infant Parents Circle"

    def test_french_ngo_prefix(self):
        """Connect carries an `ong-` prefix the directory does not."""
        assert resolve("ong-agir-pour-la-sante-rurale")["parent"] == "Agir Pour La Sante Rurale"

    def test_apostrophe_collapsed_not_split(self):
        """Connect slugifies "D'Entraide" to `dentraide`; splitting on the
        apostrophe instead yields `d-entraide` and the two never meet."""
        assert resolve("comite-dentraide-rurale-asbl")["parent"] == "Comité D'Entraide Rurale".replace("é", "e")

    def test_connectives_do_not_demote_a_match(self):
        """Connect's slug drops the "And", which once left an obviously
        identical name at review-only confidence — shown as a raw slug and
        unfindable by the name anyone knows the partner by."""
        got = resolve("peace-rural-development-initiative")
        assert got["tier"] == "same-tokens"
        assert got["short"] == "PRDI"


class TestConnectNameWins:
    """Connect's own name is a real name, and matches the directory far better
    than a slug does. It is what links some workspaces to their partner at all."""

    def test_connect_name_resolves_a_slug_that_alone_would_not(self):
        got = resolve("programme-workspace-04", connect_name="Silverbrook Health Partners ECD")
        assert got["parent"] == "Silverbrook Health Partners"
        assert "via connect name" in got["why"]

    def test_a_person_looking_slug_is_a_real_partner_when_connect_names_it(self):
        """A real partner can run a workspace whose slug is somebody's username.
        Guessing from the slug alone would file that partner's delivery as a
        personal sandbox."""
        assert resolve("aworkspacehandle", connect_name="Silverbrook Health Partners")["parent"] == (
            "Silverbrook Health Partners"
        )


class TestRefusesToGuess:
    """A wrong parent name is worse than a visible slug."""

    def test_a_near_miss_is_returned_for_review_not_displayed(self):
        """A one-letter difference the matcher cannot safely close on its own.
        The close match is offered to a human and must not surface as fact."""
        got = resolve("foreland-rural-health-trast")
        assert got["parent"] == ""
        assert got["review"] is not None
        assert "Foreland" in got["review"]["candidate"]

    def test_an_unknown_slug_yields_nothing(self):
        got = resolve("an-organisation-nobody-has-recorded")
        assert got["parent"] == ""
        assert got["tier"] in {"none", "subset", "fuzzy"}

    def test_never_deslugifies_into_a_title_cased_guess(self):
        """Mechanical title-casing reads plausibly and is wrong exactly where a
        partner's real name is stylised."""
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

    def test_an_empty_directory_names_nobody_rather_than_guessing(self):
        """The safe failure. If the import has never run, every partner renders
        as its slug — which is what an unmatched slug has always done — instead
        of the page inventing names."""
        PulsePartner.objects.all().delete()
        invalidate()
        assert resolve("frht")["parent"] == ""


class TestCuratedAliases:
    """Slugs no string rule reaches, pointed at a partner by a human on the
    directory's mapping tab and carried in ``PulsePartnerAlias``."""

    @pytest.fixture
    def alias(self):
        partner = PulsePartner.objects.get(name="Silverbrook Health Partners")
        PulsePartnerAlias.objects.create(
            slug="brookside-collective",
            partner=partner,
            why="Second workspace under the founding name; shares no stem with the current one.",
        )
        invalidate()
        return partner

    def test_an_alias_resolves_at_high_confidence(self, alias):
        got = resolve("brookside-collective")
        assert got["parent"] == alias.name
        assert got["tier"] in HIGH_CONFIDENCE
        assert got["review"] is None

    def test_an_alias_covers_the_partners_next_workspace_too(self, alias):
        """Keys match as a `key-` prefix, so a partner's next opportunity
        resolves without another edit to the directory."""
        assert resolve("brookside-collective-round-2")["parent"] == alias.name

    def test_an_alias_outranks_connect_s_own_name(self, alias):
        """A human decision beats an inference, including Connect's own name."""
        got = resolve("brookside-collective", connect_name="Foreland Rural Health Trust")
        assert got["parent"] == alias.name

    def test_an_alias_does_not_loosen_anything_else(self, alias):
        """The table is a handful of confirmed rows, not a rule. A slug that
        merely resembles one is still refused."""
        assert resolve("not-brookside-collective-at-all")["parent"] == ""

    def test_two_workspaces_reach_one_partner(self, alias):
        """The point of the mapping: one partner running two Connect workspaces,
        which the display otherwise shows as two unrelated organisations."""
        assert resolve("silverbrook-health-partners")["parent"] == alias.name
        assert resolve("brookside-collective")["parent"] == alias.name
