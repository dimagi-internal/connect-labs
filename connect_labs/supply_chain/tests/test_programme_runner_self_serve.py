"""A new programme's managers declare who runs it, with no support step (2026-10-18).

Connect is faked at the one place labs asks it -- the token and the
`opp_org_program_list` fetch -- so every rule here runs as it does on the MCP
route: a user, a stored token, no session. What a programme's manager may do,
what a partner, a labs-only name and a stranger may not, and the three shapes
an agent running a new programme uses end to end.

THIS REPOSITORY IS PUBLIC. Every organisation and person here is invented.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied

from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgMembership
from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.tests.factories import CRITERIA, QUESTIONS, make_user
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.runner import OPERATOR, PARTNER, PROGRAMME, declare, dimagi_org
from connect_labs.supply_chain.runner.models import ProgrammeRunner

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

PROGRAM = 7353
PM, LLO, STRANGER = 601, 602, 603

# Connect's answer per user, as `opp_org_program_list` gives it: a programme is
# listed only to members of the organisation that owns it, and that
# organisation's opportunities with it.
TREES: dict[str, dict] = {}


def _org(connect_id, slug, name):
    return {"id": connect_id, "slug": slug, "name": name}


def _pm_tree(opportunities=()):
    return {
        "organizations": [_org(PM, "meadow-programmes", "Meadow Programmes")],
        "programs": [{"id": PROGRAM, "name": "Financial inclusion", "organization": "meadow-programmes"}],
        "opportunities": list(opportunities),
    }


@pytest.fixture(autouse=True)
def connect():
    """Connect, faked where labs calls it: a token per user, and that user's tree."""
    TREES.clear()

    def fetch(token, force_refresh=False, owner=None):
        tree = TREES.get(token.removeprefix("tok:"))
        return None if tree is None else {k: list(v) for k, v in tree.items()}

    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", lambda user: f"tok:{user.username}"),
        patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", fetch),
        patch("connect_labs.labs.access.scopes.fetch_user_organization_data", fetch),
    ):
        yield


@pytest.fixture
def meadow():
    """The programme's managing organisation, bound to its Connect id."""
    return LabsOrg.objects.create(
        slug="meadow-programmes", name="Meadow Programmes", connect_organization_id=PM,
        connect_organization_slug="meadow-programmes",
    )  # fmt: skip


@pytest.fixture
def lakeside():
    """An implementer: its own Connect organisation, outside the programme's."""
    return LabsOrg.objects.create(
        slug="lakeside-health", name="Lakeside Health", connect_organization_id=LLO,
        connect_organization_slug="lakeside-health",
    )  # fmt: skip


@pytest.fixture
def stranger_org():
    return LabsOrg.objects.create(
        slug="far-away-ngo", name="Far Away NGO", connect_organization_id=STRANGER,
        connect_organization_slug="far-away-ngo",
    )  # fmt: skip


@pytest.fixture
def manager():
    """A member of the managing organisation -- on an agent's own address, as ACE signs in."""
    user = make_user("pm-agent", email="agent@dimagi-ai.com")
    TREES["pm-agent"] = _pm_tree()
    return user


@pytest.fixture
def plain_manager():
    """A member of the managing organisation with no Dimagi address."""
    user = make_user("pm-person", email="lead@meadow.example")
    TREES["pm-person"] = _pm_tree()
    return user


@pytest.fixture
def implementer():
    user = make_user("llo-person", email="ops@lakeside.example")
    TREES["llo-person"] = {"organizations": [_org(LLO, "lakeside-health", "Lakeside Health")]}
    return user


@pytest.fixture
def stranger():
    user = make_user("stranger", email="someone@faraway.example")
    TREES["stranger"] = {"organizations": [_org(STRANGER, "far-away-ngo", "Far Away NGO")]}
    return user


def _access(user, program_id=PROGRAM):
    """Exactly what the MCP handler builds: a user and a token, no session."""
    token = f"tok:{user.username}"
    return SupplyDataAccess(
        access_token=token, program_id=program_id, user=user, caller=Caller(user=user, access_token=token)
    )


def op(access, name, **payload):
    return call_operation(name, access, payload, channel="mcp")


def declare_as(user, org, program_id=PROGRAM):
    return op(_access(user, program_id), "programme_runner_set", org_id=org.pk)


class TestTheFirstDeclaration:
    def test_a_manager_declares_their_own_organisation(self, manager, meadow):
        result = declare_as(manager, meadow)
        assert (result["org_id"], result["your_standing"]) == (meadow.pk, PROGRAMME)
        assert ProgrammeRunner.objects.get(program_id=PROGRAM).declared_by == manager

    def test_a_manager_who_is_dimagi_may_declare_dimagi(self, manager):
        assert declare_as(manager, dimagi_org())["your_standing"] == PROGRAMME

    def test_a_manager_who_is_not_dimagi_may_not_hand_it_to_dimagi(self, plain_manager, meadow):
        with pytest.raises(PermissionDenied, match="nothing ties Dimagi"):
            declare_as(plain_manager, dimagi_org())

    def test_a_manager_may_not_name_an_organisation_nothing_ties_to_the_programme(self, manager, stranger_org):
        with pytest.raises(PermissionDenied, match="nothing ties Far Away NGO"):
            declare_as(manager, stranger_org)

    def test_a_manager_may_name_an_organisation_delivering_in_the_programme(self, manager, lakeside):
        TREES["pm-agent"] = _pm_tree([{"id": 9001, "organization": "lakeside-health", "program": PROGRAM}])
        assert declare_as(manager, lakeside)["org_id"] == lakeside.pk

    def test_a_connect_slug_two_rows_claim_matches_neither(self, manager, lakeside):
        LabsOrg.objects.create(slug="lookalike", name="Lookalike", connect_organization_id=699,
                               connect_organization_slug="lakeside-health")  # fmt: skip
        TREES["pm-agent"] = _pm_tree([{"id": 9001, "organization": "lakeside-health", "program": PROGRAM}])
        with pytest.raises(PermissionDenied, match="nothing ties"):
            declare_as(manager, lakeside)

    def test_someone_connect_does_not_list_the_programme_to_may_not(self, stranger, stranger_org):
        # Holding the programme is checked first; give them that, and the
        # declaration is still refused: holding is not managing.
        with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
            access = _access(stranger)
        with pytest.raises(PermissionDenied, match="manages it in Connect"):
            op(access, "programme_runner_set", org_id=stranger_org.pk)

    def test_a_labs_only_programme_listing_is_not_managing(self, stranger, stranger_org):
        # The same programme id folded into the caller's tree from a labs-only
        # registration: their name-bucket "owns" it, and it counts for nothing.
        TREES["stranger"] = {
            "organizations": [{**_org(STRANGER, "far-away-ngo", "Far Away NGO"), "labs_only": True}],
            "programs": [{"id": PROGRAM, "organization": "far-away-ngo", "labs_only": True}],
        }
        with pytest.raises(PermissionDenied, match="manages it in Connect"):
            declare_as(stranger, stranger_org)

    def test_a_labs_only_organisation_is_never_named(self, manager):
        bucket = LabsOrg.objects.create(slug="labs-synthetic-meadow", name="Meadow (synthetic)", labs_only=True)
        with pytest.raises(PermissionDenied, match="nothing ties"):
            declare_as(manager, bucket)

    def test_a_programme_made_since_the_cached_list_is_found_by_asking_again(self, manager, meadow):
        calls = []

        def fetch(token, force_refresh=False, owner=None):
            calls.append(force_refresh)
            return _pm_tree() if force_refresh else {"organizations": [_org(PM, "meadow-programmes", "Meadow")]}

        with patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", fetch):
            with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
                access = _access(manager)
            assert op(access, "programme_runner_set", org_id=meadow.pk)["org_id"] == meadow.pk
        # Cached first, then asked again; what follows is the standing the reply reports.
        assert calls[:2] == [False, True]

    def test_support_still_declares_anything(self, stranger_org):
        support = make_user("support", staff=True)
        with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
            access = _access(support)
        assert op(access, "programme_runner_set", org_id=stranger_org.pk)["your_standing"] == OPERATOR


class TestChangingIt:
    def test_the_runner_hands_it_to_the_implementer_it_awarded(self, manager, meadow, lakeside):
        declare_as(manager, meadow)
        call = _call(manager, meadow)
        _award(manager, call, lakeside)
        assert declare_as(manager, lakeside)["org_id"] == lakeside.pk

    def test_the_implementer_may_hand_it_back_to_who_awarded_it(self, manager, meadow, lakeside, implementer):
        call = _call(manager, meadow)
        _award(manager, call, lakeside)
        declare_as(manager, lakeside)
        assert declare_as(implementer, meadow)["org_id"] == meadow.pk

    def test_a_manager_who_does_not_run_it_may_not_change_it(self, manager, meadow, lakeside, stranger_org):
        _award(manager, _call(manager, meadow), lakeside)
        declare_as(manager, lakeside)
        with pytest.raises(PermissionDenied, match="Lakeside Health runs program"):
            declare_as(manager, meadow)

    def test_a_partner_may_not_change_it(self, manager, meadow, stranger, stranger_org):
        declare_as(manager, meadow)
        with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
            access = _access(stranger)
        assert op(access, "programme_runner_get")["your_standing"] == PARTNER
        with pytest.raises(PermissionDenied, match="Meadow Programmes runs program"):
            op(access, "programme_runner_set", org_id=stranger_org.pk)

    def test_the_runner_may_not_hand_it_to_an_unconnected_organisation(self, manager, meadow, stranger_org):
        declare_as(manager, meadow)
        with pytest.raises(PermissionDenied, match="nothing ties Far Away NGO"):
            declare_as(manager, stranger_org)

    def test_a_dimagi_run_programme_changes_only_by_support(self, manager, meadow):
        declare(PROGRAM, dimagi_org())
        with pytest.raises(PermissionDenied, match="Dimagi runs program"):
            declare_as(manager, meadow)

    def test_an_admin_granted_for_the_runner_may_change_it(self, plain_manager, meadow, lakeside):
        declare_as(plain_manager, meadow)
        _award(plain_manager, _call(plain_manager, meadow), lakeside)
        admin = make_user("lakeside-admin", email="admin@lakeside.example")
        TREES["lakeside-admin"] = {"organizations": []}
        OrgMembership.objects.create(org=lakeside, user=admin, role="admin")
        declare_as(plain_manager, lakeside)
        assert declare_as(admin, meadow)["org_id"] == meadow.pk


class TestWhatDeclaringStillDoes:
    def test_it_revokes_update_links_issued_to_the_new_runner(self, manager, meadow):
        from django.utils import timezone

        from connect_labs.supply_chain.update_links.models import UpdateLink

        link = UpdateLink.objects.create(
            program_id=PROGRAM, org=meadow, token_hash="c" * 64, expires_at=timezone.now() + timedelta(days=30)
        )
        declare_as(manager, meadow)
        link.refresh_from_db()
        assert (link.state, link.revoked_by_id) == ("revoked", manager.pk)

    def test_the_runner_row_is_then_support_only_to_change(self, manager, meadow):
        declare_as(manager, meadow)
        with pytest.raises(PermissionDenied, match="runs a program on labs"):
            op(_access(manager), "org_upsert", data={"slug": "meadow-programmes", "name": "Renamed"})


class TestStandingOnTheTokenRoute:
    """caller_org_ids works with no session: shape (b) -- a Connect-backed runner."""

    def test_a_connect_member_is_the_programme_through_their_token(self, manager, meadow):
        declare(PROGRAM, meadow)
        assert op(_access(manager), "programme_runner_get")["your_standing"] == PROGRAMME

    def test_the_org_row_an_agent_creates_and_binds_is_then_declarable(self, manager):
        created = op(
            _access(manager), "org_upsert",
            data={"slug": "meadow-programmes", "name": "Meadow Programmes", "connect_organization_id": PM,
                  "connect_organization_slug": "meadow-programmes"},
        )  # fmt: skip
        assert op(_access(manager), "programme_runner_set", org_id=created["id"])["your_standing"] == PROGRAMME

    def test_dimagi_runs_a_programme_its_agent_manages(self, manager):
        # Shape (a).
        declare_as(manager, dimagi_org())
        assert op(_access(manager), "programme_runner_get")["your_standing"] == PROGRAMME


class TestTheRunnersTeam:
    def test_a_connect_member_of_the_runner_adds_to_its_team(self, manager, meadow):
        declare_as(manager, meadow)
        colleague = make_user("colleague", email="colleague@meadow.example")
        team = op(_access(manager), "programme_team_add", email=colleague.email, role="admin")
        assert [m["email"] for m in team["members"]] == [colleague.email]

    def test_a_partner_does_not(self, manager, meadow, stranger):
        declare_as(manager, meadow)
        make_user("colleague", email="colleague@far.example")
        with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
            access = _access(stranger)
        with pytest.raises(PermissionDenied):
            op(access, "programme_team_add", email="colleague@far.example")


class TestAnImplementerReachesTheProgramme:
    def test_a_member_of_the_declared_runner_holds_a_programme_connect_does_not_list_them(
        self, manager, meadow, lakeside, implementer
    ):
        with pytest.raises(PermissionDenied, match="not accessible"):
            _access(implementer)
        _award(manager, _call(manager, meadow), lakeside)
        declare_as(manager, lakeside)
        assert op(_access(implementer), "programme_runner_get")["your_standing"] == PROGRAMME

    def test_a_stranger_still_does_not(self, manager, meadow, lakeside, stranger):
        _award(manager, _call(manager, meadow), lakeside)
        declare_as(manager, lakeside)
        with pytest.raises(PermissionDenied, match="not accessible"):
            _access(stranger)

    def test_dimagi_running_a_programme_gives_no_dimagi_address_a_hold_on_it(self):
        declare(PROGRAM, dimagi_org())
        someone = make_user("dimagi-someone", email="someone@dimagi.com")
        TREES["dimagi-someone"] = {"organizations": []}
        with pytest.raises(PermissionDenied, match="not accessible"):
            _access(someone)


# ---- the awarded-implementer flow, end to end (shape c) --------------------


def _call(user, owner):
    return SolicitationsDataAccess(user=user).create_solicitation(
        {
            "org_slug": owner.slug,
            "program_id": PROGRAM,
            "is_public": False,
            "title": "Implementer wanted",
            "description": "Deliver the programme in two districts.",
            "solicitation_type": "rfp",
            "status": "active",
            "questions": QUESTIONS,
            "evaluation_criteria": CRITERIA,
        }
    )


def _award(user, call, org):
    da = SolicitationsDataAccess(user=user)
    response = da.create_response(
        solicitation_id=call.pk,
        llo_entity_id=org.slug,
        data={"responses": {"q1": "Five years.", "q2": "9"}, "status": "submitted", "org_name": org.name},
        on_behalf=True,
    )
    return da.award_response(response.pk, reward_budget=1000, org_id=str(org.connect_organization_id))


def test_the_awarded_implementer_runs_procurement_end_to_end(manager, meadow, lakeside, implementer):
    call = _call(manager, meadow)
    assert (call.owner_org, call.listed) == (meadow, False)
    awarded = _award(manager, call, lakeside)
    assert (awarded.status, awarded.llo_entity) == ("awarded", lakeside)

    declare_as(manager, lakeside)
    access = _access(implementer)
    assert op(access, "programme_runner_get")["your_standing"] == PROGRAMME

    day = date(2026, 10, 1)
    op(access, "commodity_upsert", data={
        "slug": "chlorine-tablets", "name": "Chlorine tablets", "category": "consumable",
        "base_unit": "tablet", "pack_unit": "box", "base_per_pack": 100,
    })  # fmt: skip
    item = op(access, "item_upsert", data={
        "sku": "NADCC-100", "name": "NaDCC, box of 100", "commodity_slug": "chlorine-tablets",
        "base_unit": "tablet", "pack_unit": "box", "base_per_pack": 100,
    })  # fmt: skip
    store = op(access, "supply_point_upsert", data={
        "slug": "lakeside-store", "name": "Lakeside store", "kind": "central_store", "source": "we_recorded",
    })  # fmt: skip
    supplier = op(access, "supplier_create", data={
        "name": "Shoreline Distributors", "country": "KE", "type": "distributor", "status": "contacted",
    })["id"]  # fmt: skip
    tender = op(access, "tender_create", data={
        "label": "Chlorine tablets: 100 boxes",
        "lines": [{"commodity_slug": "chlorine-tablets", "quantity": 100, "quantity_unit": "box"}],
        "delivery_points": [{"key": "kisumu", "name": "Lakeside store", "city": "Kisumu", "country": "KE"}],
        "visibility": "private", "response_deadline": (day + timedelta(days=10)).isoformat(),
    })["id"]  # fmt: skip
    op(access, "tender_open", tender_id=tender)
    quote = op(access, "quote_record", data={
        "tender_id": tender, "supplier_id": supplier, "commodity_slug": "chlorine-tablets",
        "as_quoted_amount": "1400", "as_quoted_unit": "per_pack", "as_quoted_currency": "KES",
        "quantity_basis": 100, "quantity_basis_unit": "box", "received_on": day.isoformat(),
        "freight_basis": "included", "pack_spec_source": "stated_on_quote", "base_per_pack_stated": 100,
    })  # fmt: skip
    award = op(access, "award_create", tender_id=tender, quote_id=quote["id"], rationale="Delivered, pack stated.",
               decided_by="Programme lead", decided_on=day.isoformat())  # fmt: skip
    contract = op(access, "contract_create", data={
        "tender_id": tender, "award_id": award["id"], "supplier_id": supplier, "commodity_slug": "chlorine-tablets",
        "item_id": item["id"], "buyer_of_record": "programme_org", "buyer_org_id": lakeside.pk,
        "reference": "LH-PO-1", "status": "placed", "currency": "KES", "quantity": 100, "quantity_unit": "box",
        "unit_price": "1400", "unit_price_unit": "per_pack", "freight_basis": "included", "duties_basis": "included",
        "vat_basis": "excluded", "delivery_supply_point_id": store["id"], "source": "we_recorded",
        "signed_on": day.isoformat(),
    })  # fmt: skip
    op(access, "shipment_record", data={
        "contract_id": contract["id"], "reference": "LH-PO-1", "status": "in_transit",
        "dispatched_on": day.isoformat(), "source": "supplier_reported",
        "lines": [{"item_id": item["id"], "quantity": 100, "quantity_unit": "box"}],
    })  # fmt: skip
    op(access, "receipt_record", data={
        "contract_id": contract["id"], "supply_point_id": store["id"], "received_on": day.isoformat(),
        "source": "we_recorded", "lines": [{"quantity_accepted": 100, "quantity_unit": "box"}],
    })  # fmt: skip
    invoice = op(access, "invoice_record", data={
        "contract_id": contract["id"], "amount": "140000.00", "currency": "KES", "source": "supplier_reported",
    })  # fmt: skip
    paid = op(access, "payment_record", data={
        "invoice_id": invoice["id"], "paid_on": day.isoformat(), "amount": "140000.00", "currency": "KES",
        "source": "we_recorded",
    })  # fmt: skip
    assert paid["id"]
    # The programme's records are the implementer's, first-hand.
    from connect_labs.supply_chain.models import Contract

    assert Contract.objects.get(pk=contract["id"]).recorded_by_org == lakeside


SYNTHETIC_PROGRAM, SYNTHETIC_OPP = 10_853, 10_854


@pytest.fixture
def synthetic_programme():
    """A registered labs-only programme: no Connect manager can exist for it."""
    from connect_labs.labs.synthetic import registry
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=SYNTHETIC_OPP, program_id=SYNTHETIC_PROGRAM, labs_only=True, enabled=True,
        label="synthetic supply", allowed_domains=["@meadow.example", "@dimagi.com"],
    )  # fmt: skip
    registry.invalidate_cache()
    yield SYNTHETIC_PROGRAM
    registry.invalidate_cache()


def _opted_in(user):
    """A user who has switched synthetic programmes on, as an agent building one has."""
    user.view_synthetic_opps = True
    user.save(update_fields=["view_synthetic_opps"])
    return user


class TestASyntheticProgramme:
    """A labs-only programme has no Connect manager, so Dimagi declares itself to run it."""

    def test_a_dimagi_member_who_can_see_it_declares_dimagi(self, manager, synthetic_programme):
        _opted_in(manager)
        result = declare_as(manager, dimagi_org(), program_id=synthetic_programme)
        assert (result["org_id"], result["your_standing"]) == (dimagi_org().pk, PROGRAMME)
        assert ProgrammeRunner.objects.get(program_id=synthetic_programme).declared_by == manager

    def test_someone_who_is_not_dimagi_may_not(self, plain_manager, synthetic_programme):
        # They can see it (their domain is allowed); they are not Dimagi.
        _opted_in(plain_manager)
        with pytest.raises(PermissionDenied, match="no running organisation yet"):
            declare_as(plain_manager, dimagi_org(), program_id=synthetic_programme)

    def test_dimagi_may_not_name_another_organisation(self, manager, meadow, synthetic_programme):
        _opted_in(manager)
        with pytest.raises(PermissionDenied, match="no running organisation yet"):
            declare_as(manager, meadow, program_id=synthetic_programme)

    def test_a_dimagi_member_who_cannot_see_it_may_not(self, synthetic_programme):
        # A Dimagi associate belongs to the Dimagi organisation but is not one of
        # labs' internal domains, so the programme's allowed domains govern them.
        associate = make_user("associate", email="helper@dimagi-associate.com")
        with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
            access = _access(associate, program_id=synthetic_programme)
        with pytest.raises(PermissionDenied, match="no running organisation yet"):
            op(access, "programme_runner_set", org_id=dimagi_org().pk)

    def test_a_real_programme_with_no_manager_is_still_refused(self, stranger):
        dimagi_person = make_user("dimagi-person", email="person@dimagi.com")
        for user in (stranger, dimagi_person):
            with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
                access = _access(user)
            with pytest.raises(PermissionDenied, match=r"manages it in Connect \(or platform support\)"):
                op(access, "programme_runner_set", org_id=dimagi_org().pk)

    def test_an_existing_runner_still_changes_only_by_its_own_rule(self, manager, synthetic_programme):
        _opted_in(manager)
        declare_as(manager, dimagi_org(), program_id=synthetic_programme)
        with pytest.raises(PermissionDenied, match="runs program"):
            declare_as(manager, dimagi_org(), program_id=synthetic_programme)
