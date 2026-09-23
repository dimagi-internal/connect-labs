"""The marketplace_* read tools — what the agent reads behind a declared page.

The page hands over a SELECTION and these return the substance, so the pair has
one property worth pinning above all others: a slug the page named but the
directory does not hold must be REPORTED, not dropped. An agent asked to draft
ten emails that silently drafts nine is the failure that costs someone a partner.
"""

from __future__ import annotations

import datetime as dt

import pytest

from connect_labs.marketplace.models import OrgContact
from connect_labs.marketplace.testing import make_partner
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.mcp.tools import marketplace as tools
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create(username="agent")


@pytest.fixture
def directory():
    acme = make_partner(
        "Acme Health Partners",
        "AHP",
        countries=["Uganda", "Kenya"],
        website="https://acme.example.invalid",
        year_established=2014,
        team_size=40,
        joined_at=dt.date(2025, 2, 3),
        joined_basis="EOI submission date",
    )
    OrgContact.objects.create(
        org=acme, full_name="Ada Okonkwo", role_title="Director", email="ada@acme.example.invalid", is_main_poc=True
    )
    OrgContact.objects.create(org=acme, full_name="Ben Mwangi", email="ben@acme.example.invalid")
    beta = make_partner("Beta Care Collective", "BCC", countries=["Malawi"])

    round_ = Solicitation.objects.create(
        slug="matching-grant-2026",
        title="Matching Grant Pilot",
        solicitation_type="eoi",
        status="active",
        published_on=dt.date(2026, 3, 4),
        application_deadline=dt.date(2099, 1, 1),
        questions=[{"id": "q1", "text": "Organisation name"}],
        contact_email="rounds@example.invalid",
    )
    SolicitationResponse.objects.create(
        solicitation=round_, llo_entity=acme, source_row=2, org_name=acme.name, match_state="email"
    )
    SolicitationResponse.objects.create(
        solicitation=round_, source_row=3, org_name="Unattributed Org", match_state="unmatched"
    )
    closed = Solicitation.objects.create(
        slug="chc-2025",
        title="Community Health Campaign",
        solicitation_type="eoi",
        status="closed",
        published_on=dt.date(2025, 1, 8),
    )
    return {"acme": acme, "beta": beta, "open": round_, "closed": closed}


class TestRegistration:
    def test_both_are_read_only(self):
        """The directory is maintained in the source sheet and imported. A write
        tool here would be a second source of truth for rows a human owns."""
        for name in ("marketplace_orgs_get", "marketplace_rounds_list"):
            tool = get_tool(name)
            assert tool is not None, f"{name} is not registered"
            assert tool.is_write is False


class TestOrgsGet:
    def test_it_returns_the_rows_behind_a_selection(self, user, directory):
        result = tools.marketplace_orgs_get(user, slugs=[directory["acme"].slug, directory["beta"].slug])

        assert result["count"] == 2
        assert [o["name"] for o in result["organizations"]] == ["Acme Health Partners", "Beta Care Collective"]
        assert result["not_found"] == []

    def test_it_preserves_the_order_the_page_asked_in(self, user, directory):
        """The page's order is the order on screen, and an agent working through
        "each of these" should follow it rather than the database's."""
        wanted = [directory["beta"].slug, directory["acme"].slug]

        result = tools.marketplace_orgs_get(user, slugs=wanted)

        assert [o["slug"] for o in result["organizations"]] == wanted

    def test_a_slug_the_directory_does_not_hold_is_reported(self, user, directory):
        """The one that matters: nine emails where ten were asked for, silently,
        is worse than a named gap."""
        result = tools.marketplace_orgs_get(user, slugs=[directory["acme"].slug, "gone-from-the-sheet"])

        assert result["count"] == 1
        assert result["not_found"] == ["gone-from-the-sheet"]

    def test_contacts_are_withheld_unless_asked_for(self, user, directory):
        """Real people's addresses, landing in a transcript that persists. The
        common "what is in this round" question has no use for them."""
        result = tools.marketplace_orgs_get(user, slugs=[directory["acme"].slug])

        assert "contacts" not in result["organizations"][0]

    def test_contacts_come_back_when_they_are_the_point(self, user, directory):
        result = tools.marketplace_orgs_get(user, slugs=[directory["acme"].slug], include_contacts=True)

        contacts = result["organizations"][0]["contacts"]
        assert [c["email"] for c in contacts] == ["ada@acme.example.invalid", "ben@acme.example.invalid"]
        assert contacts[0]["is_main_poc"] is True

    def test_it_carries_the_basis_of_a_joined_date(self, user, directory):
        """Some are an exact submission date and some a cohort's publication date
        shared by everyone in it, so the date alone would imply precision it lacks.
        """
        result = tools.marketplace_orgs_get(user, slugs=[directory["acme"].slug])

        assert result["organizations"][0]["joined_at"] == "2025-02-03"
        assert result["organizations"][0]["joined_basis"] == "EOI submission date"

    def test_an_empty_selection_is_refused(self, user, directory):
        with pytest.raises(MCPToolError):
            tools.marketplace_orgs_get(user, slugs=[])

    def test_more_than_a_page_is_refused_rather_than_truncated(self, user, directory):
        """Silently returning the first hundred of three hundred would have the
        agent believe it had seen them all."""
        with pytest.raises(MCPToolError):
            tools.marketplace_orgs_get(user, slugs=[f"org-{n}" for n in range(tools.MAX_ORGS + 1)])


class TestRoundsList:
    def test_it_lists_the_rounds(self, user, directory):
        result = tools.marketplace_rounds_list(user)

        assert {r["slug"] for r in result["rounds"]} == {"matching-grant-2026", "chc-2025"}

    def test_one_round_by_slug(self, user, directory):
        result = tools.marketplace_rounds_list(user, slug="matching-grant-2026")

        assert result["count"] == 1
        assert result["rounds"][0]["title"] == "Matching Grant Pilot"
        assert result["rounds"][0]["questions"] == [{"id": "q1", "text": "Organisation name"}]
        assert result["rounds"][0]["contact_email"] == "rounds@example.invalid"

    def test_open_only_follows_the_dates_not_the_typed_status(self, user, directory):
        """A stored status is typed by hand and goes stale the day a deadline
        passes, so the page's own helper decides."""
        result = tools.marketplace_rounds_list(user, open_only=True)

        assert [r["slug"] for r in result["rounds"]] == ["matching-grant-2026"]

    def test_applicants_count_an_unattributed_submission_as_its_own(self, user, directory):
        """Assuming two unmatched submissions are the same organisation is exactly
        the judgement being deferred, so the count must not collapse them."""
        result = tools.marketplace_rounds_list(user, slug="matching-grant-2026", include_applicants=True)

        row = result["rounds"][0]
        assert row["applications"] == 2
        assert row["organisations"] == 2
        assert row["unresolved"] == 1
        outcomes = {a["name"]: a["outcome"] for a in row["applicants"]}
        assert outcomes["Unattributed Org"] == "unresolved"

    def test_an_unmatched_applicant_has_no_slug_to_read_back(self, user, directory):
        """So an agent cannot pass it to `marketplace_orgs_get` and get a
        confusing miss — there is no organisation to fetch yet."""
        result = tools.marketplace_rounds_list(user, slug="matching-grant-2026", include_applicants=True)

        unattributed = [a for a in result["rounds"][0]["applicants"] if a["name"] == "Unattributed Org"]
        assert unattributed[0]["slug"] is None

    def test_applicants_need_a_named_round(self, user, directory):
        with pytest.raises(MCPToolError):
            tools.marketplace_rounds_list(user, include_applicants=True)

    def test_an_unknown_slug_is_a_miss_not_an_empty_list(self, user, directory):
        with pytest.raises(MCPToolError):
            tools.marketplace_rounds_list(user, slug="never-existed")
