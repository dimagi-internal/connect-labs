from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflow", "0005_action_on_a_page"),
    ]

    operations = [
        migrations.CreateModel(
            name="SandboxExemption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("definition_id", models.IntegerField(unique=True)),
                ("opportunity_id", models.IntegerField(blank=True, null=True)),
                ("program_id", models.IntegerField(blank=True, null=True)),
                ("organization_id", models.CharField(blank=True, default="", max_length=100)),
                ("render_code_sha256", models.CharField(max_length=64)),
                ("reason", models.TextField()),
                ("added_by", models.CharField(max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["definition_id"],
            },
        ),
    ]
