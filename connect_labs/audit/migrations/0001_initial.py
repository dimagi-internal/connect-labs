"""Intentionally empty -- a placeholder, not a mistake.

`audit.0001_initial` is already recorded as applied in production (2025-11-07),
from a migration whose file no longer exists in this repo. This file exists only
so the migration graph can resolve that name: 0002_priorauditprojectionstate is
applied and declares a dependency on it, and Django cannot build a graph with a
dangling dependency.

The table this was originally generated to create -- PriorAuditVerdict -- moved
to 0004_priorauditverdict, which has a name the recorded history does not
already claim. Putting operations back here would not run them: Django would
skip this migration as applied, exactly as it did on 2026-08-21.
"""

from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = []
