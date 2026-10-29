from django.db import migrations, models


class Migration(migrations.Migration):
    """Entries made on the field page carry the phone's own id and clock, so retries are idempotent."""

    dependencies = [
        ("supply_chain", "0060_update_link_verification"),
    ]

    operations = [
        migrations.AddField(
            model_name="updatelinksubmission",
            name="client_id",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.AddField(
            model_name="updatelinksubmission",
            name="device_time",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="updatelinksubmission",
            name="photo_attached",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddConstraint(
            model_name="updatelinksubmission",
            constraint=models.UniqueConstraint(
                condition=models.Q(("client_id", ""), _negated=True),
                fields=("link", "client_id"),
                name="uniq_update_link_submission_client_id",
            ),
        ),
    ]
