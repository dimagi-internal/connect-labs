"""The directory screens. All data invented."""

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgContact, OrgProfile
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


@pytest.fixture
def registry(db):
    delivering = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust", short_name="FT")
    OrgProfile.objects.create(org=delivering, countries=["Kenya"])
    OrgContact.objects.create(org=delivering, email="a@example.invalid", full_name="A Person")

    bench = LabsOrg.objects.create(slug="harbourside", name="Harbourside Health Initiative")
    OrgProfile.objects.create(org=bench, countries=["Nigeria"])

    round_ = Solicitation.objects.create(
        slug="demo-2026", title="Demo round", solicitation_type="eoi", delivery_type="chc"
    )
    SolicitationResponse.objects.create(
        solicitation=round_,
        llo_entity=delivering,
        org_name="Fenwick Trust",
        source_row=2,
        match_state=SolicitationResponse.MATCH_EMAIL,
        match_basis="exact contact email",
    )
    SolicitationResponse.objects.create(
        solicitation=round_,
        org_name="Someone Else",
        source_row=3,
        match_state=SolicitationResponse.MATCH_UNMATCHED,
    )
    return {"delivering": delivering, "bench": bench, "round": round_}


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x")


@pytest.mark.django_db
class TestAccess:
    def test_the_directory_requires_a_login(self, client, registry):
        """Submission text is what an organisation wrote about itself while
        applying for work. There is no anonymous view of it."""
        for name, args in [
            ("marketplace:network", []),
            ("marketplace:unmatched", []),
            ("marketplace:organisation", ["fenwick"]),
        ]:
            response = client.get(reverse(name, args=args))
            assert response.status_code in (301, 302), name
            assert "/accounts/login" in response["Location"] or "login" in response["Location"], name


@pytest.mark.django_db
class TestDirectory:
    def test_lists_every_organisation_not_only_those_on_connect(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:network")).content.decode()
        assert "Fenwick Trust" in body
        assert "Harbourside Health Initiative" in body

    def test_search_narrows_the_list(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:network"), {"q": "Harbour"}).content.decode()
        assert "Harbourside" in body
        assert "Fenwick Trust</a>" not in body

    def test_filters_to_organisations_with_no_contact(self, client, user, registry):
        """The segment that matters for outreach: nobody to write to."""
        client.force_login(user)
        body = client.get(reverse("marketplace:network"), {"segment": "nocontact"}).content.decode()
        assert "Harbourside" in body
        assert "Fenwick Trust</a>" not in body

    def test_filters_by_the_program_an_organisation_applied_for(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:network"), {"applied": "chc"}).content.decode()
        assert "Fenwick Trust" in body
        assert "Harbourside" not in body

    def test_the_verdict_queue_is_reachable_without_being_advertised(self, client, user, registry):
        """It is our obligation, not the visitor's. The page still exists and
        still lists everything awaiting a decision; the home page just does not
        lead with a count of our own unfinished work."""
        client.force_login(user)
        assert "awaiting a verdict" not in client.get(reverse("marketplace:home")).content.decode()
        assert client.get(reverse("marketplace:unmatched")).status_code == 200


@pytest.mark.django_db
class TestOrganisationPage:
    def test_shows_profile_contacts_and_application_history(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:organisation", args=["fenwick"])).content.decode()
        assert "Fenwick Trust" in body
        assert "a@example.invalid" in body
        assert "Demo round" in body

    def test_says_plainly_when_there_is_nobody_to_write_to(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:organisation", args=["harbourside"])).content.decode()
        assert "nobody here to write to" in body

    def test_distinguishes_no_connect_delivery_from_no_attribution(self, client, user, registry):
        """An organisation with no workspace attributed has nothing to join on —
        which is not the same as never having delivered."""
        client.force_login(user)
        body = client.get(reverse("marketplace:organisation", args=["harbourside"])).content.decode()
        assert "nothing to join on" in body

    def test_an_unknown_organisation_is_a_404(self, client, user, registry):
        client.force_login(user)
        assert client.get(reverse("marketplace:organisation", args=["nope"])).status_code == 404


@pytest.mark.django_db
class TestUnmatchedQueue:
    def test_lists_only_submissions_awaiting_a_verdict(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:unmatched")).content.decode()
        assert "Someone Else" in body
        assert "Fenwick Trust" not in body


class TestTemplatesUseTheFrameworkLabsActuallyLoads:
    """Labs loads Tailwind v4 and no Bootstrap — `base.html` pulls
    `bundles/css/tailwind.css`, and `package.json` has no bootstrap dependency.

    This is worth a test because the failure is SILENT: a Bootstrap class name
    is valid HTML, renders without error, passes every text assertion in this
    file, and simply has no styling attached. All three templates in this app
    shipped to production that way.
    """

    BOOTSTRAP_ONLY = [
        "card-body",
        "card-header",
        "form-select",
        "form-control",
        "btn-primary",
        "btn-outline",
        "table-sm",
        "table-hover",
        "list-group",
        "accordion",
        "data-bs-toggle",
        "data-bs-target",
        "col-md-",
        "row g-",
        "badge bg-",
        "text-muted",
        "table-responsive",
        "form-label",
        "container-fluid",
    ]

    def _templates(self):
        from pathlib import Path

        import connect_labs

        return sorted((Path(connect_labs.__file__).parent / "templates" / "marketplace").glob("*.html"))

    def test_no_marketplace_template_uses_a_bootstrap_only_class(self):
        import re

        offences = []
        for path in self._templates():
            # Strip {% comment %} blocks: they deliberately NAME the Bootstrap
            # tokens to explain why they are not used, and matching that prose
            # would make this guard cry wolf at the very note preventing the bug.
            body = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", path.read_text(), flags=re.S)
            for token in self.BOOTSTRAP_ONLY:
                if token in body:
                    offences.append(f"{path.name}: {token!r}")
        assert not offences, "Bootstrap classes render unstyled in labs:\n  " + "\n  ".join(offences)

    def test_application_history_opens_without_javascript(self):
        """The accordion was Bootstrap-JS driven, which labs never loads, so the
        answers could not be expanded at all. <details> needs no JS."""
        organisation = next(p for p in self._templates() if p.name == "organisation.html")
        body = organisation.read_text()
        assert "<details" in body and "<summary" in body


@pytest.mark.django_db
class TestTheQueueIsActionable:
    """A review queue with no way to record a verdict is decorative: the same
    submissions sit in it after every future import."""

    def test_says_how_to_resolve_one_and_links_to_the_directory(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:unmatched")).content.decode()
        assert "EOI Response Mapping" in body
        assert "docs.google.com/spreadsheets" in body
        assert "not an LLO" in body

    def test_shows_the_exact_keys_the_mapping_tab_needs(self, client, user, registry):
        """Round slug and response row are the mapping tab's first two columns;
        showing them is what makes resolving one a copy rather than a hunt."""
        client.force_login(user)
        body = client.get(reverse("marketplace:unmatched")).content.decode()
        assert "demo-2026" in body
        assert ">3<" in body

    def test_a_dismissed_submission_leaves_the_queue(self, client, user, registry):
        client.force_login(user)
        response = SolicitationResponse.objects.get(source_row=3)
        response.match_state = SolicitationResponse.MATCH_NOT_LLO
        response.match_basis = "an individual, not an organisation"
        response.save()
        body = client.get(reverse("marketplace:unmatched")).content.decode()
        assert "Someone Else" not in body


@pytest.mark.django_db
class TestUnreadableRoundsAreVisible:
    def test_the_network_page_does_not_warn_about_ingest(self, client, user, registry):
        """Which sheets we could open is not a fact about the network."""
        Solicitation.objects.filter(slug="demo-2026").update(sa_access_state="denied")
        client.force_login(user)
        body = client.get(reverse("marketplace:network")).content.decode()
        assert "could not be read" not in body

    def test_says_nothing_when_every_round_is_readable(self, client, user, registry):
        Solicitation.objects.filter(slug="demo-2026").update(sa_access_state="ok")
        client.force_login(user)
        body = client.get(reverse("marketplace:network")).content.decode()
        assert "could not be read" not in body


@pytest.mark.django_db
class TestTheBadgeAndThePanelAgree:
    """An organisation badged "delivering" beside "no Connect workspace
    attributed to it" is two true statements that together read as a bug. It
    happened because the badge matched on NAME through pulse's resolver while
    the panel looked only at hand-curated slug attributions.
    """

    @pytest.fixture
    def delivering_org(self, db):
        from connect_labs.marketplace.testing import make_partner
        from connect_labs.pulse.models import PulseOpportunity

        org = make_partner("Foreland Rural Health Trust", "FRHT")
        PulseOpportunity.objects.create(
            opportunity_id=91,
            name="Foreland delivery",
            org_slug="foreland-rural-health-trust",
            country="UG",
            lifetime_visit_count=1234,
        )
        return org

    def test_delivery_is_found_without_a_curated_slug_attribution(self, client, user, delivering_org):
        """No OrgConnectSlug row exists — pulse resolves the workspace by name,
        and the panel must use the same resolution."""
        assert not delivering_org.connect_slugs.exists()
        client.force_login(user)
        body = client.get(reverse("marketplace:organisation", args=[delivering_org.slug])).content.decode()
        assert "Foreland delivery" in body
        assert "1234" in body
        assert "nothing to join on" not in body

    def test_an_organisation_with_no_workspace_still_says_so(self, client, user, registry):
        """The honest empty state must survive the fix."""
        client.force_login(user)
        body = client.get(reverse("marketplace:organisation", args=["harbourside"])).content.decode()
        assert "nothing to join on" in body
