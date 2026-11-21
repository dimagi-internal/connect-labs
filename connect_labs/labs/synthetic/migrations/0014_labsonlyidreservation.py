"""Reserve labs-only ids so concurrent synthetic clone jobs never share one.

A clone job read ``max(id) + 1`` minutes before registering anything, so two jobs
started together were handed the same program and opportunity id and the second
registration overwrote the first. A program id was also re-issued after a job that
registered nothing. Each id is now inserted here first; the unique ``value`` decides.

No backfill: ``next_labs_only_opp_id`` still reads existing opportunities and their
program ids, so reservations only need to cover ids handed out from now on.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("synthetic", "0013_drop_user_synthetic_dataset")]

    operations = [
        migrations.CreateModel(
            name="LabsOnlyIdReservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.IntegerField(unique=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[("program", "Program"), ("opportunity", "Opportunity")],
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Labs-only id reservation",
                "db_table": "labs_synthetic_id_reservation",
            },
        ),
    ]
