"""A criteria version's outcome rules get their own field, and each version can carry AI Observations.

Nothing is backfilled: versions written before this keep their outcome rules
inside `basis`, and `marketplace.queries.outcome_rules_of` reads them from there.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("solicitations", "0025_round_forms"),
    ]

    operations = [
        migrations.AddField(
            model_name="rubricversion",
            name="outcome_rules",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="rubricversion",
            name="observations",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="rubricversion",
            name="observations_compared_with",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="rubricversion",
            name="observations_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="rubricversion",
            name="observations_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
