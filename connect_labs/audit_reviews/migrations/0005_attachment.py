import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit_reviews", "0004_decision_revoked"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Attachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("storage_key", models.CharField(max_length=500)),
                ("filename", models.CharField(max_length=200)),
                ("content_type", models.CharField(blank=True, max_length=100)),
                ("size_bytes", models.IntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("uploaded_by", models.CharField(blank=True, max_length=200)),
                ("uploaded_at", models.DateTimeField()),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="audit_reviews.auditsource",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "labs_audit_review_attachment",
                "ordering": ["uploaded_at", "id"],
            },
        ),
    ]
