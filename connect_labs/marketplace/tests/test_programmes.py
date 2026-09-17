"""Programme tags: Connect's vocabulary, not one this app invented.

All data invented.
"""

import pytest
from django.urls import reverse

from connect_labs.marketplace import programmes, queries
from connect_labs.marketplace.testing import make_partner
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


class TestTheVocabulary:
    def test_a_known_slug_reads_as_connects_name_for_it(self):
        assert programmes.label("kmc") == "Kangaroo Mother Care"
        assert programmes.label("chc") == "Child Health Campaign"

    def test_an_unknown_slug_stays_visibly_a_code(self):
        """Pulse's rule, inherited deliberately: an invented label does not look
        uncertain, so an unrecognised slug is shown in capitals instead."""
        assert programmes.label("zzz") == "ZZZ"

    def test_the_unclassified_bucket_is_not_a_programme(self):
        """Connect's `other` means "no delivery type recorded". Treating it as a
        programme would put 264 opportunities behind a label meaning "unknown".
        """
        assert not programmes.is_programme("other")
        assert programmes.chips(["other", "kmc"]) == [{"slug": "kmc", "label": "Kangaroo Mother Care"}]

    def test_a_round_that_is_not_a_programme_says_so_rather_than_going_blank(self):
        """A matching grant is a funding instrument. "Nobody has tagged this"
        and "somebody looked and it is not a programme" are different states,
        and only the first is work outstanding."""
        assert programmes.label(programmes.PROGRAMME_NONE) == "Cross-programme"
        assert not programmes.is_programme(programmes.PROGRAMME_NONE)
        assert programmes.chips([programmes.PROGRAMME_NONE]) == []

    def test_chips_deduplicate_and_sort_by_what_is_read(self):
        assert [c["label"] for c in programmes.chips(["readers", "kmc", "readers"])] == [
            "Kangaroo Mother Care",
            "Readers Distribution",
        ]


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x")


@pytest.fixture
def one_org(db):
    org = make_partner("Lakeside Health Trust", "LHT", countries=["Kenya"], delivers=["kmc", "other"])
    round_ = Solicitation.objects.create(
        slug="rutf-2026", title="RUTF 2026 — Nigeria", status="closed", delivery_type="nutrition"
    )
    SolicitationResponse.objects.create(
        solicitation=round_, llo_entity=org, source_row=2, org_name=org.name, match_state="name"
    )
    return {"org": org, "round": round_}


@pytest.mark.django_db
class TestDeliveredComesFromPulseNotFromAForm:
    def test_an_organisation_carries_the_programmes_its_opportunities_ran(self, one_org):
        assert queries.delivered_programmes_by_org_name()["Lakeside Health Trust"] == {"kmc"}

    def test_the_unclassified_opportunity_does_not_become_a_programme(self, one_org):
        """`delivers=["kmc", "other"]` seeded two opportunities; only one names
        a programme."""
        assert "other" not in queries.delivered_programmes_by_org_name()["Lakeside Health Trust"]

    def test_delivered_and_applied_do_not_bleed_into_each_other(self, one_org):
        org = queries.all_rows_with_rounds()[0]
        assert queries.delivered_programmes_by_org_name()["Lakeside Health Trust"] == {"kmc"}
        assert queries.applied_programmes_of(org) == {"nutrition"}


@pytest.mark.django_db
class TestThePagesShowTheTags:
    def test_the_round_page_names_its_programme(self, client, user, one_org):
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["rutf-2026"])).content.decode()
        assert "Nutrition" in body

    def test_an_untagged_round_shows_no_programme_rather_than_a_wrong_one(self, client, user, one_org):
        Solicitation.objects.filter(slug="rutf-2026").update(delivery_type="")
        client.force_login(user)
        response = client.get(reverse("marketplace:round", args=["rutf-2026"]))
        assert response.context["round"].programme_label == ""

    def test_the_organisation_page_separates_delivered_from_applied(self, client, user, one_org):
        client.force_login(user)
        context = client.get(reverse("marketplace:organisation", args=[one_org["org"].slug])).context
        assert [p["label"] for p in context["delivered_programmes"]] == ["Kangaroo Mother Care"]
        assert [p["label"] for p in context["applied_programmes"]] == ["Nutrition"]

    def test_the_network_row_no_longer_claims_an_flw_count(self, client, user, one_org):
        """It was self-reported free text — "20-100 FLWs", "Medium", "50-80
        sampling sites capacity" — so the column could be displayed but never
        compared, and a number in a table reads as comparable."""
        client.force_login(user)
        body = client.get(reverse("marketplace:network")).content.decode()
        # Paired with a positive assertion on purpose: a test that only checks
        # an absence passes just as happily when the whole row stops rendering.
        assert "Kangaroo Mother Care" in body
        assert "FLWS" not in body


class TestWaterIsAKnownProgramme:
    """Chlorine dispensers needed a programme and Connect had none that fitted.
    `water` was added deliberately rather than the round being tagged `cholera`,
    which was the nearest guess and would have been wrong.
    """

    def test_water_reads_as_a_name_not_a_code(self):
        assert programmes.label("water") == "Water"

    def test_water_is_a_real_programme_so_it_can_be_filtered_on(self):
        assert programmes.is_programme("water")
        assert programmes.chips(["water"]) == [{"slug": "water", "label": "Water"}]

    def test_a_new_type_with_no_opportunities_yet_still_works_on_the_applied_side(self, db):
        """`water` is new, so pulse may carry no opportunities under it for a
        while. The round's tag must not depend on delivery having happened."""
        org = make_partner("Riverbank Water Trust", "RWT", countries=["Nigeria"])
        round_ = Solicitation.objects.create(
            slug="chlorine-2025", title="Chlorine 2025", status="closed", delivery_type="water"
        )
        SolicitationResponse.objects.create(
            solicitation=round_, llo_entity=org, source_row=2, org_name=org.name, match_state="name"
        )
        fetched = next(o for o in queries.all_rows_with_rounds() if o.pk == org.pk)
        assert queries.applied_programmes_of(fetched) == {"water"}
        assert queries.delivered_programmes_by_org_name().get("Riverbank Water Trust") is None


class TestEveryLiveProgrammeHasAName:
    """Three real delivery types ran with no label and rendered as TMS,
    CONVERSATION and CHOLERA. The fallback to capitals is deliberate — a code
    is visibly a code — but it is a prompt to go and ask, not a destination.
    """

    def test_the_last_three_unnamed_types_are_named(self):
        assert programmes.label("tms") == "Turmeric Market Survey"
        assert programmes.label("conversation") == "Low-resource languages"
        assert programmes.label("cholera") == "Cholera"

    def test_every_delivery_type_seen_in_production_has_a_name(self):
        """The 15 slugs `PulseProgram.delivery_type` actually carried on
        2026-09-17. A new one appearing is not a failure — it renders as a code
        and this test then says whose name is missing."""
        live = {
            "chc",
            "malaria",
            "interview",
            "nutrition",
            "ecd",
            "ace",
            "hhs",
            "kmc",
            "readers",
            "ivp",
            "wellme",
            "mbw",
            "cholera",
            "tms",
            "conversation",
        }
        # Membership, not shape: ACE's real name IS its slug in capitals, so
        # comparing against `.upper()` calls a named programme unnamed.
        unnamed = live - programmes.known_slugs()
        assert unnamed == set(), f"these production delivery types have no name: {sorted(unnamed)}"
