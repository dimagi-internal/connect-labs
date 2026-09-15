"""Organisations and supply points, driven through the browser.

THIS REPOSITORY IS PUBLIC. Every organisation and figure here is invented.

Two things here that the other write-screen suites do not have to think about:

  * an organisation is labs-wide, so a test asserting a scoped queryset would
    be asserting the wrong thing — the correct behaviour is that every
    programme sees every organisation;
  * a supply point carries provenance, and `source` is required by the schema
    BEFORE `stamp_provenance` runs. So the screen asks for it, and these tests
    pin that it reaches the row rather than being silently defaulted.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import SupplyPoint

pytestmark = pytest.mark.django_db

PROGRAM = 10503


@pytest.fixture
def user(client, django_user_model):
    """Signed in AND attributable.

    A supply point carries provenance, so `stamp_provenance` refuses to write
    one for a caller that acts for no organisation. A dimagi.com address is
    what makes this caller Dimagi staff, which is the rule `resolve_org`
    applies -- not a detail of the fixture, but the thing being relied on.
    """
    account = django_user_model.objects.create_user(username="grace", password="x", email="grace@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import form_views, network_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "network_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def org():
    return LabsOrg.objects.create(slug="nutriset", name="Nutriset", country="FR")


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="central-store",
        name="Central store",
        kind="central_store",
        source="we_recorded",
    )


def point_post(**overrides):
    payload = {
        "slug": "kano-regional",
        "name": "Kano regional store",
        "kind": "regional_store",
        "parent": "",
        "managed_by_org": "",
        "opportunity_id": "",
        "connect_username": "",
        "admin_area": "Kano State",
        "latitude": "",
        "longitude": "",
        "min_months_of_stock": "",
        "max_months_of_stock": "",
        "status": "active",
        "source": "we_recorded",
    }
    payload.update(overrides)
    return payload


class TestOrganisations:
    def test_an_organisation_is_created(self, scoped):
        response = scoped.post(
            reverse("supply_chain:org_create"),
            {"slug": "nutriset", "name": "Nutriset", "short_name": "", "country": "fr"},
        )
        assert response.status_code == 302
        made = LabsOrg.objects.get(slug="nutriset")
        assert made.name == "Nutriset"
        assert made.country == "FR"

    def test_a_slug_already_in_use_is_refused_rather_than_overwriting(self, scoped, org):
        """`org_upsert` is an upsert: this would have rewritten Nutriset."""
        response = scoped.post(
            reverse("supply_chain:org_create"),
            {"slug": "nutriset", "name": "Somebody else", "short_name": "", "country": ""},
        )
        assert response.status_code == 200
        assert "Nutriset" in response.content.decode()
        org.refresh_from_db()
        assert org.name == "Nutriset"

    def test_editing_binds_it_to_connect(self, scoped, org):
        response = scoped.post(
            reverse("supply_chain:org_edit", args=[org.pk]),
            {
                "slug": "nutriset",
                "name": "Nutriset",
                "short_name": "",
                "country": "FR",
                "connect_organization_id": "4242",
            },
        )
        assert response.status_code == 302
        org.refresh_from_db()
        assert org.connect_organization_id == 4242
        assert org.is_linked

    def test_editing_keeps_the_slug_fixed(self, scoped, org):
        """Other rows find an unlinked organisation by slug, so changing one mid-life loses it."""
        scoped.post(
            reverse("supply_chain:org_edit", args=[org.pk]),
            {"slug": "something-else", "name": "Renamed", "short_name": "", "country": "FR"},
        )
        assert not LabsOrg.objects.filter(slug="something-else").exists()
        org.refresh_from_db()
        assert org.name == "Renamed"

    def test_aliases_survive_an_edit_that_does_not_show_them(self, scoped):
        """Aliases are curated by hand for slugs no rule can reach, so an edit must not clear them."""
        org = LabsOrg.objects.create(slug="nutriset", name="Nutriset", aliases=["nutriset-sas"])
        scoped.post(
            reverse("supply_chain:org_edit", args=[org.pk]),
            {"slug": "nutriset", "name": "Nutriset SAS", "short_name": "", "country": "FR"},
        )
        org.refresh_from_db()
        assert org.aliases == ["nutriset-sas"]

    def test_the_directory_shows_every_organisation_not_just_this_programmes(self, scoped):
        """One registry for the whole of labs. A scoped list here would be the bug."""
        LabsOrg.objects.create(slug="a", name="Organisation A")
        LabsOrg.objects.create(slug="b", name="Organisation B")
        body = scoped.get(reverse("supply_chain:organisations")).content.decode()
        assert "Organisation A" in body
        assert "Organisation B" in body


class TestMergingOrganisations:
    def test_two_rows_become_one_and_the_folded_slug_survives_as_an_alias(self, scoped):
        keep = LabsOrg.objects.create(slug="nutriset", name="Nutriset")
        merge = LabsOrg.objects.create(slug="nutriset-sas", name="Nutriset SAS")

        response = scoped.post(reverse("supply_chain:org_merge"), {"keep": keep.pk, "merge": merge.pk})
        assert response.status_code == 302

        assert not LabsOrg.objects.filter(pk=merge.pk).exists()
        keep.refresh_from_db()
        assert "nutriset-sas" in keep.aliases, "old references still have to resolve"

    def test_merging_a_row_into_itself_is_refused(self, scoped, org):
        """`merge_orgs` owns this rule. What is proved here is that its refusal
        reaches the page rather than 500ing — the form does not restate it."""
        response = scoped.post(reverse("supply_chain:org_merge"), {"keep": org.pk, "merge": org.pk})
        assert response.status_code == 200
        assert "merged into itself" in response.content.decode()
        assert LabsOrg.objects.filter(pk=org.pk).exists()

    def test_two_different_connect_organisations_cannot_be_merged(self, scoped):
        """Two Connect ids means two organisations, whatever the names look like."""
        keep = LabsOrg.objects.create(slug="a", name="A", connect_organization_id=1)
        merge = LabsOrg.objects.create(slug="b", name="B", connect_organization_id=2)

        response = scoped.post(reverse("supply_chain:org_merge"), {"keep": keep.pk, "merge": merge.pk})
        assert response.status_code == 200, "a refusal re-renders rather than redirecting"
        assert LabsOrg.objects.filter(pk=merge.pk).exists()


class TestSupplyPoints:
    def test_a_point_is_created_in_this_programme(self, scoped):
        response = scoped.post(reverse("supply_chain:supply_point_create"), point_post())
        assert response.status_code == 302
        made = SupplyPoint.objects.get(slug="kano-regional")
        assert made.program_id == PROGRAM
        assert made.admin_area == "Kano State"

    def test_how_you_know_is_recorded_on_the_row(self, scoped):
        """`source` has no default, so a record cannot read as first-hand by omission."""
        scoped.post(reverse("supply_chain:supply_point_create"), point_post(source="partner_reported"))
        assert SupplyPoint.objects.get(slug="kano-regional").source == "partner_reported"

    def test_a_workers_own_holding_must_name_the_connect_user(self, scoped):
        """The model refuses this three layers down, in `full_clean`, where it
        arrives as a banner over a form of thirteen fields. The form's own
        check exists to put it on the field that is wrong -- so that, not the
        refusal, is what this asserts.

        Asserting `"connect_username" in body` proved nothing: the field's own
        name is in the page whether or not it carries an error.
        """
        response = scoped.post(
            reverse("supply_chain:supply_point_create"),
            point_post(slug="a-worker", name="A worker", kind="user_held"),
        )
        assert response.status_code == 200
        assert not SupplyPoint.objects.filter(slug="a-worker").exists()
        assert "connect_username" in response.context["form"].errors

    def test_a_workers_own_holding_with_a_username_is_accepted(self, scoped):
        response = scoped.post(
            reverse("supply_chain:supply_point_create"),
            point_post(slug="a-worker", name="A worker", kind="user_held", connect_username="worker-01"),
        )
        assert response.status_code == 302
        assert SupplyPoint.objects.get(slug="a-worker").is_user_held

    def test_a_stock_band_the_wrong_way_round_is_refused(self, scoped):
        response = scoped.post(
            reverse("supply_chain:supply_point_create"),
            point_post(min_months_of_stock="3", max_months_of_stock="1"),
        )
        assert response.status_code == 200
        assert not SupplyPoint.objects.filter(slug="kano-regional").exists()

    def test_a_parent_is_sent_under_the_name_the_schema_uses(self, scoped, store):
        """The form field is `parent`; the operation's schema calls it `parent_supply_point_id`."""
        response = scoped.post(reverse("supply_chain:supply_point_create"), point_post(parent=store.pk))
        assert response.status_code == 302
        assert SupplyPoint.objects.get(slug="kano-regional").parent_id == store.pk

    def test_the_parent_picker_offers_only_this_programmes_points(self, scoped, store):
        """No apostrophe in the foreign name, deliberately.

        The first version called it "Somebody else's store", and the `not in`
        assertion passed whether or not the queryset was scoped -- Django
        escapes the apostrophe to `&#x27;`, so the raw string was never going
        to be in the page either way. A test that cannot fail is worse than no
        test; this one goes red when the scope filter is removed.
        """
        SupplyPoint.objects.create(
            program_id=99999,
            slug="theirs",
            name="A store in another programme",
            kind="central_store",
            source="we_recorded",
        )
        body = scoped.get(reverse("supply_chain:supply_point_create")).content.decode()
        assert "Central store" in body
        assert "A store in another programme" not in body

    def test_a_point_is_not_offered_as_its_own_parent(self, scoped, store):
        body = scoped.get(reverse("supply_chain:supply_point_edit", args=[store.pk])).content.decode()
        assert f'<option value="{store.pk}"' not in body

    def test_a_point_from_another_programme_is_not_found(self, scoped):
        theirs = SupplyPoint.objects.create(
            program_id=99999, slug="theirs", name="Theirs", kind="facility", source="we_recorded"
        )
        assert scoped.get(reverse("supply_chain:supply_point_edit", args=[theirs.pk])).status_code == 404


class TestTheScreensAreReachable:
    def test_the_network_page_groups_points_by_kind(self, scoped, store):
        SupplyPoint.objects.create(
            program_id=PROGRAM,
            slug="a-worker",
            name="A worker",
            kind="user_held",
            connect_username="worker-01",
            source="we_recorded",
        )
        body = scoped.get(reverse("supply_chain:network")).content.decode()
        assert "Central stores" in body
        assert "Field workers" in body
        assert body.index("Central stores") < body.index("Field workers"), "read top-down, as the network runs"

    def test_the_network_page_offers_adding_and_editing(self, scoped, store):
        body = scoped.get(reverse("supply_chain:network")).content.decode()
        assert reverse("supply_chain:supply_point_create") in body
        assert reverse("supply_chain:supply_point_edit", args=[store.pk]) in body

    def test_an_empty_network_says_what_to_do_rather_than_nothing(self, scoped):
        body = scoped.get(reverse("supply_chain:network")).content.decode()
        assert "Nothing here yet" in body
        assert reverse("supply_chain:supply_point_create") in body

    def test_the_supplier_directory_links_to_organisations(self, scoped):
        body = scoped.get(reverse("supply_chain:suppliers")).content.decode()
        assert reverse("supply_chain:organisations") in body

    def test_the_organisation_directory_offers_adding_editing_and_merging(self, scoped, org):
        body = scoped.get(reverse("supply_chain:organisations")).content.decode()
        assert reverse("supply_chain:org_create") in body
        assert reverse("supply_chain:org_merge") in body
        assert reverse("supply_chain:org_edit", args=[org.pk]) in body

    def test_the_network_tab_is_in_the_nav_and_reads_as_current(self, scoped):
        body = scoped.get(reverse("supply_chain:network")).content.decode()
        assert reverse("supply_chain:network") in body

    def test_organisations_read_as_part_of_suppliers_rather_than_unhighlighting_the_nav(self, scoped):
        """A page under no tab makes the nav read as though you have left the domain."""
        from connect_labs.supply_chain.navigation import TAB_FOR_VIEW

        assert TAB_FOR_VIEW["supply_chain:organisations"] == "supply_chain:suppliers"


class TestACallerWhoActsForNobody:
    """Provenance is compulsory below the contract, so a caller who belongs to
    no organisation cannot write a supply point. The refusal is right; what was
    wrong was the instruction it carried."""

    @pytest.fixture
    def stranger(self, client, django_user_model, monkeypatch):
        from connect_labs.supply_chain import form_views, network_views, views  # noqa: F401
        from connect_labs.supply_chain.api_views import _access as real_access

        account = django_user_model.objects.create_user(username="stranger", password="x", email="a@example.invalid")
        client.force_login(account)

        def _scoped(request):
            access = real_access(request)
            access.program_id = PROGRAM
            return access

        for module in ("form_views", "views", "network_views"):
            monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
            monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
        return client

    def test_the_write_is_refused(self, stranger):
        response = stranger.post(reverse("supply_chain:supply_point_create"), point_post())
        assert response.status_code == 200
        assert not SupplyPoint.objects.filter(slug="kano-regional").exists()

    def test_the_message_points_at_a_screen_rather_than_at_an_mcp_tool(self, stranger):
        """The domain's own message names `org_upsert`, which is right for an
        API caller and useless to somebody looking at a browser."""
        body = stranger.post(reverse("supply_chain:supply_point_create"), point_post()).content.decode()
        assert "Suppliers" in body and "Organisations" in body


class TestTheScreensRefuseWithoutAProgramme:
    def test_the_network_screens_ask_for_a_programme(self, client, user):
        for name in ("supply_chain:network", "supply_chain:supply_point_create"):
            response = client.get(reverse(name))
            assert response.status_code == 200, name
            assert "No programme selected" in response.content.decode(), name

    def test_organisations_do_not_need_one_because_they_are_labs_wide(self, client, user):
        """The whole point of one registry: it is not a programme's data."""
        assert client.get(reverse("supply_chain:organisations")).status_code == 200
        assert client.get(reverse("supply_chain:org_create")).status_code == 200


class TestThePayloadShape:
    """What actually crosses the boundary.

    Two things here are invisible to a test that posts a form and then reads
    the database, because both spellings currently reach the same column:
    which key the parent is sent under, and whether the Django-flavoured one is
    left behind beside it. Mutating either left the whole browser-level suite
    green — the same gap `TestThePayloadBoundary` exists for in
    test_write_screens.py.
    """

    def _form(self, **overrides):
        from connect_labs.labs.access.scopes import SYSTEM
        from connect_labs.supply_chain.data_access import SupplyDataAccess
        from connect_labs.supply_chain.network_forms import SupplyPointForm

        access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        form = SupplyPointForm(point_post(**overrides), access=access)
        assert form.is_valid(), form.errors
        return form

    def test_the_parent_is_sent_under_the_schemas_name(self, store):
        payload = self._form(parent=store.pk).payload()
        assert payload["parent_supply_point_id"] == store.pk

    def test_and_djangos_own_spelling_is_not_sent_beside_it(self, store):
        """Which of `parent` and `parent_id` wins inside `update_or_create` is
        Django kwarg-ordering, not a documented rule. Sending one key is how
        this stops depending on it."""
        assert "parent_id" not in self._form(parent=store.pk).payload()

    def test_the_manager_needs_no_rename(self, org):
        """`managed_by_org_id` is what `to_payload` produces and what the
        schema names, so a rename here would be a bug, not a fix."""
        payload = self._form(managed_by_org=org.pk).payload()
        assert payload["managed_by_org_id"] == org.pk
