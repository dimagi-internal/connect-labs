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
            human={"demo:7": (other, "link", "confirmed by hand: trading name")},
        )
        assert got == other
        assert state == SolicitationResponse.MATCH_HUMAN
        assert "confirmed by hand" in basis

    def test_an_empty_submission_matches_nothing(self, org):
        got, state, _ = match_submission(Sub(), by_email={}, by_name={normalise("Fenwick Trust"): org})
        assert got is None
        assert state == SolicitationResponse.MATCH_UNMATCHED


@pytest.mark.django_db
class TestNotAnLloVerdict:
    def test_a_dismissal_takes_the_submission_out_of_the_queue(self):
        """A submission that is not an organisation must be closable, or it sits
        in the review queue for ever looking like outstanding work."""
        got, state, basis = match_submission(
            Sub(org_name="Someone's personal note", source_row=9),
            by_email={},
            by_name={},
            human={"demo:9": (None, "not_an_llo", "an individual, not an organisation")},
        )
        assert got is None
        assert state == SolicitationResponse.MATCH_NOT_LLO
        assert "individual" in basis


class TestTrailingAcronymAndSpacing:
    @pytest.mark.parametrize(
        "submitted,directory",
        [
            ("Friends of the Community Organization FOCO", "Friends Of The Community Organization"),
            ("Community Health Alliance Uganda - CHAU", "Community Health Alliance Uganda"),
            ('Afghan Social Marketing Organization "ASMO"', "Afghan Social Marketing Organization"),
            ("RESILIENT ACTION ORGANISATION- RAO", "RESILIENT ACTION ORGANISATION"),
            ("Save Mothers and Children Initiative SMACI", "Save Mothers and Children Initiative"),
            # The directory's own spelling of one organisation.
            (
                "Kyetume Community Based Heath care Programme-KCBHCP",
                "Kyetume Community Based Heath Care Programme-Kcbhcp",
            ),
        ],
    )
    def test_a_trailing_acronym_restating_the_name_is_dropped(self, submitted, directory):
        assert normalise(submitted) == normalise(directory)

    @pytest.mark.parametrize("name", ["Tearfund UK", "Health Access Initiative HAI Kenya", "Plan International USA"])
    def test_a_final_word_that_is_not_the_initials_stays(self, name):
        assert normalise(name).split()[-1] == name.split()[-1].lower()


@pytest.mark.django_db
class TestLooserButStillCertain:
    def test_spacing_is_folded_when_it_names_exactly_one_organisation(self):
        org = LabsOrg.objects.create(slug="nd", name="N'Domakeh Federation")
        got, state, _ = match_submission(
            Sub(org_name="Ndomakeh Federation"), by_email={}, by_name={normalise(org.name): org}
        )
        assert got == org
        assert state == SolicitationResponse.MATCH_NAME

    def test_an_earlier_submissions_email_matches_when_the_name_agrees(self):
        org = LabsOrg.objects.create(slug="eha", name="EHA Clinics (REACH Program)")
        got, state, basis = match_submission(
            Sub(org_name="EHA CLINIC REACH PROGRAM", emails=["x@eha.invalid"]),
            by_email={},
            by_name={},
            by_earlier_email={"x@eha.invalid": org},
        )
        assert got == org
        assert state == SolicitationResponse.MATCH_EMAIL
        assert "earlier matched submission" in basis

    def test_an_earlier_submissions_email_is_not_trusted_under_a_different_name(self):
        """Same inbox, different organisation name: possibly a sister
        organisation, and that is a person's call."""
        org = LabsOrg.objects.create(slug="nama", name="Nama Wellness Community Centre")
        got, state, _ = match_submission(
            Sub(org_name="Nama Health Impact", emails=["x@nama.invalid"]),
            by_email={},
            by_name={},
            by_earlier_email={"x@nama.invalid": org},
        )
        assert got is None
        assert state == SolicitationResponse.MATCH_UNMATCHED

    def test_a_blank_name_is_matched_on_the_earlier_email(self):
        """The RUTF RFP form never asks for the organisation's name."""
        org = LabsOrg.objects.create(slug="cbi", name="Care Best Initiative (CBI)")
        got, _, _ = match_submission(
            Sub(org_name="", emails=["x@cbi.invalid"]),
            by_email={},
            by_name={},
            by_earlier_email={"x@cbi.invalid": org},
        )
        assert got == org
