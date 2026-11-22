import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workflow", "0008_public_run_share"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonalPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(max_length=60)),
                ("definition_id", models.IntegerField(blank=True, null=True)),
                ("shared_people", models.JSONField(blank=True, default=list)),
                ("shared_scopes", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="personal_pages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("owner", "slug"), name="personal_page_owner_slug")],
            },
        ),
    ]
