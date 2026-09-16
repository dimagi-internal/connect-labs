"""Binding submissions to organisations. All data invented."""
import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.matching import match_submission, normalise
from connect_labs.solicitations.local_models import SolicitationResponse


class Sub:
    def __init__(self, org_name="", emails=(), source_row=2, round_slug="demo"):
        self.org_name, self.emails, self.source_row, self.round_slug = org_name, list(emails), source_row, round_slug


class TestNormalise:
    def test_folds_case_accents_and_punctuation(self):
        assert normalise("Santé Générale!") == normalise("sante generale")

    def test_folds_legal_and_sector_suffixes(self):
        assert normalise("Fenwick Trust") == normalise("Fenwick")

    def test_keeps_genuinely_different_names_apart(self):
        assert normalise("Fenwick") != normalise("Fenwick International")


@pytest.mark.django_db
class TestMatchSubmission:
    @pytest.fixture
    def org(self):
        return LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust")

    def test_matches_on_an_exact_contact_email(self, org):
        got, state, basis = match_submission(
            Sub(emails=["a@example.invalid"]), by_email={"a@example.invalid": org}, by_name={}
        )
        assert got == org
        assert state == SolicitationResponse.MATCH_EMAIL
        assert "a@example.invalid" in basis

    def test_matches_on_an_exact_normalised_name(self, org):
        got, state, _ = match_submission(
            Sub(org_name="  FENWICK TRUST "), by_email={}, by_name={normalise("Fenwick Trust"): org}
        )
        assert got == org
        assert state == SolicitationResponse.MATCH_NAME

    def test_a_near_miss_stays_unmatched(self, org):
        """The rule the whole design turns on: a misattributed submission puts
        one organisation's words on another organisation's record."""
        got, state, basis = match_submission(
            Sub(org_name="Fenwick Trust International"), by_email={}, by_name={normalise("Fenwick Trust"): org}
        )
        assert got is None
        assert state == SolicitationResponse.MATCH_UNMATCHED
        assert basis == ""

    def test_a_human_verdict_outranks_every_inference(self, org):
        other = LabsOrg.objects.create(slug="other", name="Other Body")
        got, state, basis = match_submission(
            Sub(org_name="Fenwick Trust", emails=["a@example.invalid"], source_row=7),
            by_email={"a@example.invalid": org},
            by_name={normalise("Fenwick Trust"): org},
            human={"demo:7": (other, "confirmed by hand: trading name")},
        )
        assert got == other
        assert state == SolicitationResponse.MATCH_HUMAN
        assert "confirmed by hand" in basis

    def test_an_empty_submission_matches_nothing(self, org):
        got, state, _ = match_submission(Sub(), by_email={}, by_name={normalise("Fenwick Trust"): org})
        assert got is None
        assert state == SolicitationResponse.MATCH_UNMATCHED
