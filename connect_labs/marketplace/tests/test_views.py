"""The directory screens. All data invented."""
import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgContact, OrgProfile
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


@pytest.fixture
def registry(db):
    delivering = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust", short_name="FT")
    OrgProfile.objects.create(org=delivering, countries=["Kenya"], sectors=["Health"], flws_managed=80)
    OrgContact.objects.create(org=delivering, email="a@example.invalid", full_name="A Person")

    bench = LabsOrg.objects.create(slug="harbourside", name="Harbourside Health Initiative")
    OrgProfile.objects.create(org=bench, countries=["Nigeria"], sectors=["Nutrition"])

    round_ = Solicitation.objects.create(slug="demo-2026", title="Demo round", solicitation_type="eoi")
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
            ("marketplace:directory", []),
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
        body = client.get(reverse("marketplace:directory")).content.decode()
        assert "Fenwick Trust" in body
        assert "Harbourside Health Initiative" in body

    def test_search_narrows_the_list(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:directory"), {"q": "Harbour"}).content.decode()
        assert "Harbourside" in body
        assert "Fenwick Trust</a>" not in body

    def test_filters_to_organisations_with_no_contact(self, client, user, registry):
        """The segment that matters for outreach: nobody to write to."""
        client.force_login(user)
        body = client.get(reverse("marketplace:directory"), {"status": "no_contact"}).content.decode()
        assert "Harbourside" in body
        assert "Fenwick Trust</a>" not in body

    def test_filters_by_the_round_an_organisation_applied_to(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:directory"), {"applied": "demo-2026"}).content.decode()
        assert "Fenwick Trust" in body
        assert "Harbourside" not in body

    def test_surfaces_the_unmatched_queue(self, client, user, registry):
        client.force_login(user)
        body = client.get(reverse("marketplace:directory")).content.decode()
        assert "awaiting a verdict" in body


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
