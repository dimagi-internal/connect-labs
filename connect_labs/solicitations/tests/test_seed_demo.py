import pytest
from django.core.management import call_command

from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
from connect_labs.solicitations.tests.factories import make_user

pytestmark = pytest.mark.django_db


def test_seed_demo_is_idempotent_open_and_invented():
    admin = make_user("demo-admin")
    call_command("solicitations_seed_demo", "--admin", "demo-admin")
    call_command("solicitations_seed_demo")
    calls = Solicitation.objects.filter(slug__startswith="demo-solicitations-")
    assert calls.count() == 3
    assert SolicitationResponse.objects.filter(solicitation__in=calls).count() == 2
    org = calls.first().owner_org
    assert org.labs_only and "(demo)" in org.name
    # Unlisted: kept out of the directory, so nobody real finds and answers them.
    assert not calls.filter(listed=True).exists()
    assert SolicitationsDataAccess().get_public_solicitations(open_only=True) == []
    assert all(SolicitationsDataAccess().get_solicitation_by_id(c.pk) == c for c in calls)
    # the --admin member manages the demo calls and reads their responses
    access = SolicitationsDataAccess(user=admin)
    survey = calls.get(slug="demo-solicitations-household-survey")
    assert len(access.get_responses_for_solicitation(survey.pk)) == 2
    for response in SolicitationResponse.objects.filter(solicitation__in=calls):
        assert response.submitted_by_email.endswith(".example")
