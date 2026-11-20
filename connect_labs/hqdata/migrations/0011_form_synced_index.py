# Built CONCURRENTLY: the forms table holds millions of rows and is written by every sync, so a plain CREATE INDEX
# would block syncs while it builds.

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("hqdata", "0010_campaign_records"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="hqform",
            index=models.Index(fields=["project_id", "synced_at"], name="hqform_project_synced"),
        ),
    ]
