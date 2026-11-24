from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_reviews", "0003_settings_versions"),
    ]

    operations = [
        migrations.AddField(
            model_name="decision",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="decision",
            name="revoked_by",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="decision",
            name="revoke_note",
            field=models.TextField(blank=True),
        ),
        # An undone "Not this week" is kept; a new one on the same proposal must still be allowed.
        migrations.RemoveConstraint(model_name="decision", name="audit_review_decision_once"),
        migrations.AddConstraint(
            model_name="decision",
            constraint=models.UniqueConstraint(
                condition=models.Q(revoked_at__isnull=True),
                fields=("source", "kind", "run_id", "entity"),
                name="audit_review_decision_once",
            ),
        ),
    ]
