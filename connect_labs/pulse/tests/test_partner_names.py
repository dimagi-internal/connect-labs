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
        """A real partner can run a workspace whose slug is somebody's username.
        Guessing from the slug alone would file that partner's delivery as a
        personal sandbox. The handle here is synthetic — the real ones are
        colleagues' usernames, and the behaviour is what the test is for."""
        assert resolve("aworkspacehandle", connect_name="Pachi Malawi")["parent"] == (
            "Parent and Child Health Initiative (PACHI)"
        )


class TestRefusesToGuess:
    """A wrong parent name is worse than a visible slug."""

    def test_a_near_miss_is_returned_for_review_not_displayed(self):
        """A one-letter difference the matcher cannot safely close on its own.
        `arewa-health-trust-initiative` used to land here too, against the
        master list's "INIATIVE" typo; it is now an alias, so this asserts the
        rule on a slug nobody has confirmed."""
        got = resolve("janna-health-fundation")
        assert got["parent"] == ""
        assert got["tier"] == "fuzzy"
        assert got["review"] is not None
        assert "Janna" in got["review"]["candidate"]

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


class TestCuratedAliases:
    """Slugs no string rule reaches, resolved by a human and carried as data.

    Deliberately written without partner names in the assertions. The names are
    reviewed in the directory sheet and snapshotted into partner_master.json;
    what these tests own is the MECHANISM — precedence, prefix matching, and
    that the table cannot silently rot — not the contents.
    """

    def test_every_alias_resolves_at_high_confidence(self):
        from connect_labs.pulse.partner_names import _aliases

        assert _aliases(), "the snapshot carries no aliases"
        for prefix, name in _aliases():
            got = resolve(prefix)
            assert got["parent"] == name
            assert got["tier"] in HIGH_CONFIDENCE
            assert got["review"] is None

    def test_an_alias_covers_the_partners_next_workspace_too(self):
        """Keys match as a `key-` prefix, so a partner's next opportunity
        resolves without another edit to the table."""
        from connect_labs.pulse.partner_names import _aliases

        prefix, name = _aliases()[0]
        assert resolve(f"{prefix}-some-future-opportunity")["parent"] == name

    def test_an_alias_outranks_connect_s_own_name(self):
        """A human decision beats an inference, including Connect's name."""
        from connect_labs.pulse.partner_names import _aliases

        prefix, name = _aliases()[0]
        assert resolve(prefix, connect_name="Something Else Entirely")["parent"] == name

    def test_every_alias_target_exists_in_the_master_snapshot(self):
        """The guard against drift: refreshing the snapshot must not leave an
        alias pointing at a name that is no longer in the master list."""
        from connect_labs.pulse.partner_names import _aliases, _candidates

        names = {c["name"] for c in _candidates()}
        missing = [prefix for prefix, name in _aliases() if name not in names]
        assert not missing, f"alias slugs whose target is absent from the master list: {missing}"

    def test_every_alias_states_its_evidence(self):
        """An entry without a reason is a guess someone will later trust."""
        import json

        from connect_labs.pulse.partner_names import DATA

        for entry in json.loads(DATA.read_text())["aliases"]:
            assert entry.get("why", "").strip(), entry["slug_prefix"]

    def test_an_alias_does_not_loosen_anything_else(self):
        """The table is a handful of entries, not a rule. A slug that merely
        looks similar to one is still refused."""
        from connect_labs.pulse.partner_names import _aliases

        prefix, _ = _aliases()[0]
        assert resolve(f"not-{prefix}-at-all")["parent"] == ""


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


def test_connectives_do_not_demote_a_match():
    """The defect that hid PRIDE: Connect's slug drops the "And" from
    "Peace Restoration And Integral Global Development Initiative", which
    left an obviously-identical name at review-only confidence -- shown as
    a raw slug and unfindable by the name anyone knows the partner by."""
    from connect_labs.pulse.partner_names import resolve

    r = resolve("peace-restoration-integral-global-development-initiative")
    assert r["tier"] == "same-tokens"
    assert r["short"] == "PRIDE"
    assert r["parent"] == "Peace Restoration And Integral Global Development Initiative"
