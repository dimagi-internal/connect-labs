import datetime as dt
import importlib

import pytest
from django.apps import apps

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated

migration = importlib.import_module("connect_labs.labs.synthetic.migrations.0012_unmark_replayed_clones")


def _row(opp_id, *, cloned_from, updated):
    row = SyntheticOpportunity.objects.create(
        opportunity_id=opp_id,
        labs_only=True,
        gdrive_folder_id=f"f{opp_id}",
        generated_folder_id=f"f{opp_id}",
        cloned_from_opportunity_id=cloned_from,
    )
    SyntheticOpportunity.objects.filter(pk=row.pk).update(updated_at=updated)
    return row


@pytest.mark.django_db
def test_a_clone_written_while_numbers_were_replayed_is_no_longer_generated():
    """A clone written from 2026-10-14 may hold real cases' numbers; one written before,
    or an opp that is not a clone, keeps its mark."""
    replayed = _row(19_001, cloned_from=874, updated=dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC))
    before = _row(19_002, cloned_from=874, updated=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    not_a_clone = _row(19_003, cloned_from=None, updated=dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC))

    migration.forward(apps, None)

    assert not is_generated(SyntheticOpportunity.objects.get(pk=replayed.pk))
    assert is_generated(SyntheticOpportunity.objects.get(pk=before.pk))
    assert is_generated(SyntheticOpportunity.objects.get(pk=not_a_clone.pk))
