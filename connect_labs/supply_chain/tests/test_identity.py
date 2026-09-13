"""Who is writing, and how we know.

The point of this module is that a caller cannot *say* who it is. Every
provenance-bearing row carries `recorded_by_party` and a `source`, both of
which used to arrive in the payload -- so a partner could record a receipt as
though we had witnessed it and nothing would know. What is tested here is
mostly the refusals: an unknowable caller, a caller belonging to nothing, and
a caller whose organisations match two acting parties at once.
"""

from unittest.mock import patch

import pytest

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.identity import IdentityUnresolved, caller_org_ids, resolve_party, source_for
from connect_labs.supply_chain.models import Party

pytestmark = pytest.mark.django_db

PROGRAM = 10505
SCOPE = f"prog:{PROGRAM}"


def _dimagi_user():
    """A user the shared `is_dimagi_user` recognises."""

    class _User:
        email = "sophie@dimagi.com"
        is_authenticated = True

    return _User()


def _party(slug, kind, connect_organization_id):
    return Party.objects.create(
        scope_key=SCOPE,
        slug=slug,
        name=slug.title(),
        kind=kind,
        connect_organization_id=connect_organization_id,
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

        Only integer ids can match a party, because
        `Party.connect_organization_id` is an IntegerField. So a slug is
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
        which the stamping layer turns into a refusal naming party_upsert."""
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


class TestWhichParty:
    def test_a_caller_resolves_to_the_party_its_organisation_acts_as(self):
        _party("dimagi", "programme_org", 7)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            assert resolve_party(access).slug == "dimagi"

    def test_a_partner_resolves_to_its_own_party_not_ours(self):
        _party("dimagi", "programme_org", 7)
        _party("kano-llo", "partner_org", 42)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            party = resolve_party(access)
        assert (party.slug, party.kind) == ("kano-llo", "partner_org")

    def test_an_organisation_with_no_party_here_resolves_to_nothing(self):
        """Belonging to some Connect org is not the same as acting in THIS
        programme. Returning our own party would attribute their record to us."""
        _party("dimagi", "programme_org", 7)
        access, request = _session_access([999])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            assert resolve_party(access) is None

    def test_a_party_in_another_programme_is_not_reachable(self):
        Party.objects.create(
            scope_key="prog:10506",
            slug="elsewhere",
            name="Elsewhere",
            kind="partner_org",
            connect_organization_id=7,
        )
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            assert resolve_party(access) is None

    def test_two_matching_parties_refuse_rather_than_pick(self):
        """Choosing one would attribute the row to an organisation the user
        never named. The message says what would settle it."""
        _party("llo-a", "partner_org", 7)
        _party("llo-b", "partner_org", 8)
        access, request = _session_access([7, 8])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                resolve_party(access)
        assert "recorded_by_party_id" in str(caught.value)
        assert "llo-a" in str(caught.value) and "llo-b" in str(caught.value)

    def test_dimagi_staff_act_for_the_programme_without_an_org_match(self):
        """The rule that replaced org-matching for us.

        Matching `Party.connect_organization_id` could never work in a
        labs-only programme: a synthetic organisation is identified by slug
        while that column is an integer. Rather than special-case demo data,
        Dimagi staff resolve to the programme's own party by ACL -- the same
        one `SyntheticOpportunity.is_accessible_to` already grants them.
        """
        _party("dimagi", "programme_org", None)
        _party("kano-llo", "partner_org", 8)
        access = SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user())
        assert resolve_party(access).slug == "dimagi"

    def test_dimagi_staff_in_a_programme_with_no_party_of_ours_resolve_to_nothing(self):
        """Which the stamping layer turns into a refusal naming party_upsert
        -- the state programme 10063 was in, where no setup step had ever
        created the programme's own party."""
        _party("kano-llo", "partner_org", 8)
        access = SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user())
        assert resolve_party(access) is None

    def test_two_parties_of_ours_is_a_configuration_error(self):
        _party("dimagi", "programme_org", None)
        _party("dimagi-two", "programme_org", None)
        access = SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user())
        with pytest.raises(IdentityUnresolved, match="more than one programme_org"):
            resolve_party(access)

    def test_a_synthetic_programme_needs_no_special_case(self):
        """The point of the rewrite. A labs-only programme's organisation has
        a slug where an integer would be, so org-matching is impossible there
        -- and provenance must not behave differently in demo data, or the
        demo proves something the real system cannot do."""
        _party("dimagi", "programme_org", None)
        access = SupplyDataAccess(program_id=10_600, user=_dimagi_user())
        # A different (labs-only) programme: no party of ours there, and the
        # answer is an honest None rather than a crash or a wrong match.
        assert resolve_party(access) is None


class TestSource:
    def test_a_kind_implies_how_the_fact_reached_us(self):
        assert source_for(Party(kind="programme_org")) == "we_recorded"
        assert source_for(Party(kind="partner_org")) == "partner_reported"
        assert source_for(Party(kind="supplier")) == "supplier_reported"

    def test_an_agency_does_not_get_a_source_guessed_for_it(self):
        """SOURCES has no term for an agency's report, and calling it a
        partner's would misdescribe it. Those callers state their source."""
        assert source_for(Party(kind="agency")) is None
        assert source_for(None) is None


class TestStamping:
    """What `call_operation` does with the resolved party.

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
            "buyer_party_id": 1,
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
        being refused for lacking a party."""
        access, request = _session_access([7])
        payload = {"data": {"name": "Northwind"}}
        assert self._stamp(access, payload, "supplier_create") == payload

    def test_an_unknowable_caller_is_left_alone_to_declare_its_own(self):
        """The management-command route. Refusing here would turn a missing
        argument into a permission error, and the commands that write
        provenance already pass their party."""
        access = SupplyDataAccess(access_token="local", program_id=PROGRAM)
        payload = self._contract(source="partner_reported", recorded_by_party_id=3)
        assert self._stamp(access, payload) == payload

    def test_a_knowable_caller_with_no_party_here_is_refused(self):
        access, request = _session_access([999])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                self._stamp(access, self._contract())
        assert "party_upsert" in str(caught.value)

    def test_the_party_and_source_are_stamped_from_the_session(self):
        party = _party("dimagi", "programme_org", 7)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract())
        assert stamped["data"]["recorded_by_party_id"] == party.pk
        assert stamped["data"]["source"] == "we_recorded"

    def test_a_partner_is_stamped_as_reporting_not_as_witnessing(self):
        party = _party("kano-llo", "partner_org", 42)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract())
        assert stamped["data"]["recorded_by_party_id"] == party.pk
        assert stamped["data"]["source"] == "partner_reported"

    def test_a_partner_cannot_claim_we_recorded_it(self):
        """The defect this whole change exists to close: `we_recorded` asserts
        that WE saw it, and it was previously accepted from the payload."""
        _party("kano-llo", "partner_org", 42)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                self._stamp(access, self._contract(source="we_recorded"))
        assert "first-hand" in str(caught.value)

    def test_a_partner_cannot_attribute_a_record_to_another_party(self):
        _party("kano-llo", "partner_org", 42)
        other = _party("someone-else", "partner_org", 43)
        access, request = _session_access([42])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            with pytest.raises(IdentityUnresolved) as caught:
                self._stamp(access, self._contract(recorded_by_party_id=other.pk))
        assert "another party" in str(caught.value)

    def test_we_may_record_on_a_partners_behalf(self):
        """Not a privilege -- it is the normal case. Sophie enters what the
        LLO told her, and the row has to say the LLO reported it."""
        _party("dimagi", "programme_org", 7)
        llo = _party("kano-llo", "partner_org", 42)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract(recorded_by_party_id=llo.pk, source="partner_reported"))
        assert stamped["data"]["recorded_by_party_id"] == llo.pk
        assert stamped["data"]["source"] == "partner_reported"

    def test_a_caller_supplied_source_is_not_overwritten(self):
        """A document is stronger evidence than the caller's own word, and a
        derived source must not downgrade it."""
        _party("dimagi", "programme_org", 7)
        access, request = _session_access([7])
        with patch("connect_labs.labs.context.get_org_data", return_value=request.org_data):
            stamped = self._stamp(access, self._contract(source="document"))
        assert stamped["data"]["source"] == "document"


class TestRefusalsReachTheCaller:
    """A provenance refusal is a bad request, not a server error.

    Found on labs, not in tests: attaching a document to a quote in programme
    10063 returned 500. The refusal was CORRECT -- that programme has no
    parties, so the write cannot be attributed to anyone -- but
    `IdentityUnresolved` subclassed `Exception`, and the API dispatch maps
    only `jsonschema.ValidationError` and `ValueError` to 400. So every
    refusal built in #1777 was a server error, and the message naming the fix
    ("add the party with party_upsert") never reached anybody.

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

        # A knowable caller belonging to an organisation with no party here:
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
        assert "party_upsert" in response.json()["error"]


class TestPartyScope:
    """Attribution must not depend on how the party happened to be created.

    `scope_key` is `org:<id>` when an organisation is in context and
    `prog:<id>` otherwise, so a party written by an MCP import (no
    organisation) lands under `prog:` while a web request with an
    organisation selected reads `org:`. Reading only the caller's scope made
    a Dimagi user's write refuse with "add the party with party_upsert" when
    the party was right there under the other key.

    Raised by CodeRabbit on #1784. Not reachable through the API today --
    organisation_id was not reaching the scope on those requests, verified
    against labs -- but it depends on middleware behaviour rather than on
    anything this module controls.
    """

    def test_a_party_written_under_the_programme_scope_is_found_from_an_org_context(self):
        Party.objects.create(
            scope_key=f"prog:{PROGRAM}", slug="programme", name="Programme team", kind="programme_org"
        )
        # A NUMERIC organisation, which is the reachable case: a synthetic
        # org's slug is dropped by `data_access.scope_key`'s `_as_int`, so a
        # labs-only programme always resolves to `prog:` regardless.
        with_org = SupplyDataAccess(organization_id=77, program_id=PROGRAM, user=_dimagi_user())
        assert with_org.scope_key == "org:77", "precondition: the scopes differ"
        assert resolve_party(with_org).slug == "programme"

    def test_a_party_written_under_the_org_scope_is_still_found(self):
        Party.objects.create(scope_key="org:77", slug="programme", name="Programme team", kind="programme_org")
        with_org = SupplyDataAccess(organization_id=77, program_id=PROGRAM, user=_dimagi_user())
        assert resolve_party(with_org).slug == "programme"

    def test_another_programmes_party_is_not_borrowed(self):
        Party.objects.create(scope_key=f"prog:{PROGRAM + 1}", slug="elsewhere", name="Elsewhere", kind="programme_org")
        access = SupplyDataAccess(program_id=PROGRAM, user=_dimagi_user())
        assert resolve_party(access) is None
