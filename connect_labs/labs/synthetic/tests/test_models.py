import pytest

from connect_labs.labs.synthetic.models import SyntheticOpportunity

pytestmark = pytest.mark.django_db


def test_cloned_from_field_persists():
    row = SyntheticOpportunity.objects.create(
        opportunity_id=10500, gdrive_folder_id="f", labs_only=True, cloned_from_opportunity_id=523
    )
    row.refresh_from_db()
    assert row.cloned_from_opportunity_id == 523
