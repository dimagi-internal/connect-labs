import pytest

from connect_labs.labs.synthetic.models import SyntheticOpportunity

pytestmark = pytest.mark.django_db


def test_cloned_from_field_persists():
    row = SyntheticOpportunity.objects.create(
        opportunity_id=10500, gdrive_folder_id="f", labs_only=True, cloned_from_opportunity_id=523
    )
    row.refresh_from_db()
    assert row.cloned_from_opportunity_id == 523


def test_an_allowed_domain_matches_that_domain_not_a_longer_one():
    from connect_labs.labs.synthetic.models import email_in_domains

    assert email_in_domains("a@partner.org", ["partner.org"])
    assert email_in_domains("a@partner.org", ["@partner.org"])
    assert email_in_domains("a@field.partner.org", ["partner.org"])
    assert not email_in_domains("a@evilpartner.org", ["partner.org"])
    assert not email_in_domains("a@evilpartner.org", ["@partner.org"])
    assert not email_in_domains("partner.org", ["partner.org"])
    assert not email_in_domains(None, ["partner.org"])
