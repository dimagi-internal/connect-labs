"""Who is writing, and how we know.

The point of this module is that a caller cannot *say* who it is. Every
provenance-bearing row carries `recorded_by_org` and a `source`, both of
which used to arrive in the payload -- so a partner could record a receipt as
though we had witnessed it and nothing would know. What is tested here is
mostly the refusals: an unknowable caller, a caller belonging to nothing, and
a caller whose organisations match two rows at once.
"""

from unittest.mock import patch

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.identity import IdentityUnresolved, caller_org_ids, resolve_org, source_for

pytestmark = pytest.mark.django_db

PROGRAM = 10505
SCOPE = f"prog:{PROGRAM}"


def _dimagi_user():
    """A user the shared `is_dimagi_user` recognises."""

    class _User:
        email = "sophie@dimagi.com"
        is_authenticated = True

    return _User()


def _org(slug, kind, connect_organization_id, connect_organization_slug=""):
    """An organisation. `kind` is accepted and ignored: it was a
    per-programme role crammed onto the org, and now lives on the purchase
    (`Contract.buyer_of_record`)."""
    return LabsOrg.objects.create(
        slug=slug,
        name=slug.title(),
        connect_organization_id=connect_organization_id,
        connect_organization_slug=connect_organization_slug,
    )


class _Request:
    """Only what identity reads: labs_context and a user."""

    def __init__(self, org_ids, user=None):
        self.labs_context = {"program_id": PROGRAM}
        self.user = user
        self._org_ids = org_ids

    @property
    def org_data(self):
        return {"organizations": [{"id": i} for i in self._org_ids]}


def _session_access(org_ids):
    request = _Request(org_ids)
    access = SupplyDataAccess(program_id=PROGRAM, request=request)
    return access, request


class TestWhoIsAsking:
    def test_a_command_has_nobody_to_ask_and_says_so(self):
        """None and the empty set must not be conflated: one means there is
        nobody to ask, the other means we asked and the answer was nothing.
        A command that cannot say who it acts for has to be told."""
        access = SupplyDataAccess(access_token="local", program_id=PROGRAM)
        assert caller_org_ids(access) is None

    def test_the_session_route_needs_no_round_trip(self):
        access, request = _session_access([7, 9])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data) as org_data:
            assert caller_org_ids(access) == {7, 9}
        assert org_data.called

    def test_the_mcp_route_resolves_through_the_users_connect_token(self):
        """No session on that route, so the same list comes from the token --
        cached with a TTL upstream, so a write is not a round trip."""
        user = object()
        access = SupplyDataAccess(program_id=PROGRAM, user=user)
        with patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="tok"), patch(
            "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data",
            return_value={"organizations": [{"id": 11}]},
        ):
            assert caller_org_ids(access) == {11}

    def test_a_synthetic_orgs_slug_id_does_not_crash_the_derivation(self):
        """Labs folds labs-only synthetic opportunities into the user's org
        list with `"id": org_slug` -- a STRING, not an integer (see
        labs/context.py `_merge_labs_only_opps`). `int()` over that list
        raises, so every provenance write by a user entitled to see synthetic
        opps would have been a 500 rather than a stamped row.

        Only integer ids can match a row, because
        `LabsOrg.connect_organization_id` is an IntegerField. So a slug is
        skipped, not coerced and not crashed on.
        """
        access, request = _session_access([])
        org_data = {
            "organizations": [
                {"id": "labs-synthetic-connect-rutf", "slug": "labs-synthetic-connect-rutf", "labs_only": True},
                {"id": 7, "slug": "dimagi"},
            ]
        }
        with patch("connect_labs.labs.context.get_org_data", return_value=org_data):
            assert caller_org_ids(access) == {7}

    def test_a_user_with_only_synthetic_orgs_belongs_to_nothing_matchable(self):
        """Not a crash, and not silently permissive: an honest empty set,
        which the stamping layer turns into a refusal naming org_upsert."""
        access, request = _session_access([])
        org_data = {"organizations": [{"id": "labs-synthetic-x", "slug": "labs-synthetic-x"}]}
        with patch("connect_labs.labs.context.get_org_data", return_value=org_data):
            assert caller_org_ids(access) == set()

    def test_a_failed_org_fetch_is_unknown_rather_than_empty(self):
        """A network blip is not a revoked permission. The empty set would
        read as "belongs to nothing" and get reported as a permission error."""
        user = object()
        access = SupplyDataAccess(program_id=PROGRAM, user=user)
        with patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="tok"), patch(
            "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=None
        ):
            assert caller_org_ids(access) is None


class TestWhichOrganisation:
    def test_a_caller_resolves_to_the_organisation_it_acts_as(self):
        _org("dimagi", "programme_org", 7)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            assert resolve_org(access).slug == "dimagi"

    def test_a_partner_resolves_to_its_own_party_not_ours(self):
        _org("dimagi", "programme_org", 7)
        _org("kano-llo", "partner_org", 42)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            org = resolve_org(access)
        assert org.slug == "kano-llo"

    def test_an_organisation_not_on_file_resolves_to_nothing(self):
        """Belonging to some Connect org is not the same as acting in THIS
        programme. Returning our own organisation would attribute their record to us."""
        _org("dimagi", "programme_org", 7)
        access, request = _session_access([999])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            assert resolve_org(access) is None

    def test_two_matching_organisations_refuse_rather_than_pick(self):
        """Choosing one would attribute the row to an organisation the user
        never named. The message says what would settle it."""
        _org("llo-a", "partner_org", 7)
        _org("llo-b", "partner_org", 8)
        access, request = _session_access([7, 8])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                resolve_org(access)
        assert "recorded_by_org_id" in str(caught.value)
        assert "llo-a" in str(caught.value) and "llo-b" in str(caught.value)

    def test_dimagi_staff_act_for_the_programme_without_an_org_match(self):
        """The rule that replaced org-matching for us.

        Matching on the Connect id could never work in a
        labs-only programme: a synthetic organisation is identified by slug
        while that column is an integer. Rather than special-case demo data,
        Dimagi staff resolve to Dimagi by ACL -- the same
        one `SyntheticOpportunity.is_accessible_to` already grants them.
        """
        _org("dimagi", "programme_org", None)
        _org("kano-llo", "partner_org", 8)
        access = SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user())
        assert resolve_org(access).slug == "dimagi"

    def test_dimagi_staff_resolve_to_dimagi_even_before_a_row_exists(self):
        """Which the stamping layer turns into a refusal naming org_upsert
        -- the state programme 10063 was in, where no setup step had ever
        created a row for us."""
        _org("kano-llo", "partner_org", 8)
        access = SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user())
        # Dimagi always resolves to Dimagi, creating the row on first use:
        # an organisation labs already acts as is not something to wait for.
        assert resolve_org(access).slug == "dimagi"

    def test_the_same_organisation_answers_in_every_programme(self):
        """The point of the rewrite. Dimagi is Dimagi in programme 10505 and
        in 10600 -- one row, not one per programme -- so attribution cannot
        depend on which programme a setup step happened to run in."""
        here = resolve_org(SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user()))
        there = resolve_org(SupplyDataAccess(program_id=10_600, user=_dimagi_user()))
        assert here.pk == there.pk == LabsOrg.objects.get(slug="dimagi").pk


class TestSource:
    def test_a_kind_implies_how_the_fact_reached_us(self):
        assert source_for(LabsOrg(slug="dimagi")) == "we_recorded"
        assert source_for(LabsOrg(slug="kano-llo")) == "partner_reported"

    def test_nothing_acting_has_no_source(self):
        assert source_for(None) is None


class TestStamping:
    """What `call_operation` does with the resolved organisation.

    The asymmetry tested here is deliberate and is not a privilege: we record
    a partner's receipt on their behalf routinely -- that is what
    `partner_reported` is for -- so the programme's own staff may attribute a
    row to another party. A partner may not, because a partner attributing a
    row to us would make its own claim read as first-hand.
    """

    def _contract(self, **data):
        payload = {
            "round_id": 1,
            "supplier_id": 2,
            "commodity_slug": "rutf",
            "buyer_of_record": "partner_org",
            "buyer_org_id": 1,
            "quantity": "10",
            "unit_price": "10.00",
            "currency": "USD",
        }
        payload.update(data)
        return {"data": payload}

    def _stamp(self, access, payload, operation_name="contract_create"):
        from connect_labs.supply_chain.identity import stamp_provenance
        from connect_labs.supply_chain.operations import get_operation

        return stamp_provenance(access, get_operation(operation_name), payload)

    def test_a_read_operation_is_untouched(self):
        access, request = _session_access([7])
        assert self._stamp(access, {"round_id": 1}, "contract_list") == {"round_id": 1}

    def test_a_procurement_write_is_untouched_because_it_records_no_provenance(self):
        """Provenance is compulsory BELOW the contract (section 17.3), so
        suppliers, rounds, quotes and outreach carry none and must not start
        being refused for lacking an organisation."""
        access, request = _session_access([7])
        payload = {"data": {"name": "Northwind"}}
        assert self._stamp(access, payload, "supplier_create") == payload

    def test_an_unknowable_caller_is_left_alone_to_declare_its_own(self):
        """The management-command route. Refusing here would turn a missing
        argument into a permission error, and the commands that write
        provenance already pass their organisation."""
        access = SupplyDataAccess(access_token="local", program_id=PROGRAM)
        payload = self._contract(source="partner_reported", recorded_by_org_id=3)
        assert self._stamp(access, payload) == payload

    def test_a_knowable_caller_with_no_organisation_on_file_is_refused(self):
        access, request = _session_access([999])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                self._stamp(access, self._contract())
        assert "org_upsert" in str(caught.value)

    def test_the_organisation_and_source_are_stamped_from_the_session(self):
        org = _org("dimagi", "programme_org", 7)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract())
        assert stamped["data"]["recorded_by_org_id"] == org.pk
        assert stamped["data"]["source"] == "we_recorded"

    def test_a_partner_is_stamped_as_reporting_not_as_witnessing(self):
        org = _org("kano-llo", "partner_org", 42)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract())
        assert stamped["data"]["recorded_by_org_id"] == org.pk
        assert stamped["data"]["source"] == "partner_reported"

    def test_a_partner_cannot_claim_we_recorded_it(self):
        """The defect this whole change exists to close: `we_recorded` asserts
        that WE saw it, and it was previously accepted from the payload."""
        _org("kano-llo", "partner_org", 42)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                self._stamp(access, self._contract(source="we_recorded"))
        assert "first-hand" in str(caught.value)

    def test_a_partner_cannot_attribute_a_record_to_another_organisation(self):
        _org("kano-llo", "partner_org", 42)
        other = _org("someone-else", "partner_org", 43)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                self._stamp(access, self._contract(recorded_by_org_id=other.pk))
        assert "another organisation" in str(caught.value)

    def test_we_may_record_on_a_partners_behalf(self):
        """Not a privilege -- it is the normal case. Sophie enters what the
        LLO told her, and the row has to say the LLO reported it."""
        _org("dimagi", "programme_org", 7)
        llo = _org("kano-llo", "partner_org", 42)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract(recorded_by_org_id=llo.pk, source="partner_reported"))
        assert stamped["data"]["recorded_by_org_id"] == llo.pk
        assert stamped["data"]["source"] == "partner_reported"

    def test_a_caller_supplied_source_is_not_overwritten(self):
        """A document is stronger evidence than the caller's own word, and a
        derived source must not downgrade it."""
        _org("dimagi", "programme_org", 7)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract(source="document"))
        assert stamped["data"]["source"] == "document"


class TestRefusalsReachTheCaller:
    """A provenance refusal is a bad request, not a server error.

    Found on labs, not in tests: attaching a document to a quote in programme
    10063 returned 500. The refusal was CORRECT -- that programme has no
    organisations, so the write cannot be attributed to anyone -- but
    `IdentityUnresolved` subclassed `Exception`, and the API dispatch maps
    only `jsonschema.ValidationError` and `ValueError` to 400. So every
    refusal built in #1777 was a server error, and the message naming the fix
    ("add the organisation with org_upsert") never reached anybody.

    The tests written for that work all called `stamp_provenance` directly
    and asserted the raise, which is why they passed while the thing was
    unusable through either surface.
    """

    def test_the_class_is_a_bad_request(self):
        assert issubclass(IdentityUnresolved, ValueError)

    @pytest.mark.django_db
    def test_the_api_answers_400_and_names_the_fix(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="sophie", password="x")
        client.force_login(user)

        # A knowable caller whose organisation is not on file:
        # exactly programme 10063's state.
        org_data = {"organizations": [{"id": 7, "slug": "dimagi"}]}
        # Scoped directly: what is under test is the exception-to-status
        # mapping, not whether the middleware admits a labs-only programme.
        scoped = SupplyDataAccess(program_id=PROGRAM, user=user)
        with patch("connect_labs.labs.context.get_org_data", return_value=org_data), patch(
            "connect_labs.supply_chain.api_views._access", return_value=scoped
        ):
            response = client.post(
                f"/supply/api/document_attach/?program_id={PROGRAM}",
                data={
                    "data": {
                        "kind": "other",
                        "source": "we_recorded",
                        "external_url": "https://example.test/e.pdf",
                    }
                },
                content_type="application/json",
            )

        assert response.status_code == 400, response.status_code
        assert "org_upsert" in response.json()["error"]


class TestSyntheticOrgsMatchToo:
    """A labs-only organisation is identified by slug, and now matches on it.

    This replaces a class of tests about which SCOPE an organisation lived under.
    Parties were scope-keyed, so the same organisation existed as different
    rows in different programmes and a lookup could miss the one the writer
    created. `LabsOrg` is one registry for all of labs, so the question no
    longer exists -- which is better than answering it.
    """

    def test_a_partner_matches_on_its_slug_when_it_has_no_connect_id(self):
        """The case that was structurally impossible before: an integer
        column cannot match `labs-synthetic-kano`."""
        LabsOrg.objects.create(slug="kano-llo", name="Kano partner", connect_organization_slug="labs-synthetic-kano")
        access, request = _session_access([])
        org_data = {"organizations": [{"id": "labs-synthetic-kano", "slug": "labs-synthetic-kano"}]}
        with patch("connect_labs.labs.context.get_org_data", return_value=org_data):
            assert resolve_org(access).slug == "kano-llo"

    def test_an_unknown_organisation_still_resolves_to_nothing(self):
        LabsOrg.objects.create(slug="kano-llo", name="Kano partner", connect_organization_slug="labs-synthetic-kano")
        access, request = _session_access([])
        org_data = {"organizations": [{"id": "labs-synthetic-other", "slug": "labs-synthetic-other"}]}
        with patch("connect_labs.labs.context.get_org_data", return_value=org_data):
            assert resolve_org(access) is None


class TestReviewFindings1791:
    """Five findings on the LabsOrg change, all in an hour's fast work.

    Two mattered: a signed-in caller could be trusted to attribute a record
    to anyone, and an id could be matched against another organisation's
    stale slug.
    """

    def test_a_signed_in_caller_is_never_trusted_to_attribute_a_record(self):
        """The bypass. `caller_org_ids` returned None both for "nobody to
        ask" (a management command) and for "the org fetch failed", and
        stamping treated None as "left alone to declare its own party". So a
        network blip made a signed-in user unknowable and their claimed
        `recorded_by_org_id` and `source` were taken at face value -- which
        is self-attribution, the one thing this exists to stop.
        """
        from connect_labs.supply_chain.identity import stamp_provenance
        from connect_labs.supply_chain.operations import get_operation

        # A PARTNER user. Dimagi staff may attribute on a partner's behalf by
        # design, so they are not the case this protects.
        class _Partner:
            email = "ops@kano-llo.example"
            username = "kano-ops"
            is_authenticated = True

        access = SupplyDataAccess(program_id=PROGRAM, user=_Partner())
        payload = {"data": {"kind": "other", "source": "we_recorded", "recorded_by_org_id": 999}}
        with patch(
            "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=None
        ), patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="tok"):
            with pytest.raises(IdentityUnresolved):
                stamp_provenance(access, get_operation("document_attach"), payload)

    def test_a_command_with_no_user_at_all_still_declares_its_own(self):
        """The case the None was for, kept working: an operator with a shell."""
        from connect_labs.supply_chain.identity import stamp_provenance
        from connect_labs.supply_chain.operations import get_operation

        access = SupplyDataAccess(access_token="local", program_id=PROGRAM)
        payload = {"data": {"kind": "other", "source": "partner_reported", "recorded_by_org_id": 3}}
        assert stamp_provenance(access, get_operation("document_attach"), payload) == payload

    def test_an_id_is_not_matched_against_another_orgs_stale_slug(self):
        """Ids and slugs were tested independently, so a caller's
        organisation 999 could match a LINKED row through some other
        organisation's old name."""
        LabsOrg.objects.create(
            slug="acme", name="Acme", connect_organization_id=42, connect_organization_slug="old-acme"
        )
        access, request = _session_access([])
        org_data = {"organizations": [{"id": 999, "slug": "old-acme"}]}
        with patch("connect_labs.labs.context.get_org_data", return_value=org_data):
            assert resolve_org(access) is None

    def test_a_linked_organisation_answers_on_its_id_alone(self):
        """`matches()` fell through to the slug when the caller had no id,
        contradicting the rule its own docstring states."""
        org = LabsOrg.objects.create(
            slug="acme", name="Acme", connect_organization_id=42, connect_organization_slug="old-acme"
        )
        assert org.matches(organization_id=42)
        assert not org.matches(slug="old-acme")

    @pytest.mark.django_db
    def test_a_renamed_organisation_is_updated_rather_than_duplicated(self):
        """Keyed only on the slug, a rename looked like a new organisation and
        the insert hit the unique Connect id -- a rename failing as a database
        error."""
        access = SupplyDataAccess(program_id=PROGRAM)
        first = access.upsert_org({"slug": "acme", "name": "Acme", "connect_organization_id": 42})
        second = access.upsert_org({"slug": "acme-renamed", "name": "Acme Ltd", "connect_organization_id": 42})
        assert first.pk == second.pk
        assert second.slug == "acme-renamed" and second.name == "Acme Ltd"
        assert LabsOrg.objects.count() == 1
