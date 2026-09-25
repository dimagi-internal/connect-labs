from django.db import migrations, models


class Migration(migrations.Migration):
    """How a supply point got its coordinates (stock/services/placement.py).

    Each column carries a DATABASE default as well as a model one. Django applies
    `default` in Python, so a checkout or a running task whose model predates
    this migration inserts without these columns -- and without `db_default`
    that insert fails NOT NULL. It did, for every other worktree sharing a local
    database, and would on labs for an old task still serving during a rolling
    deploy.
    """

    dependencies = [
        ("supply_chain", "0021_portfolio"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplypoint",
            name="location_label",
            field=models.CharField(blank=True, db_default="", default="", max_length=255),
        ),
        migrations.AddField(
            model_name="supplypoint",
            name="location_precision",
            field=models.CharField(blank=True, db_default="", default="", max_length=8),
        ),
        migrations.AddField(
            model_name="supplypoint",
            name="location_source",
            field=models.CharField(
                blank=True,
                choices=[("recorded", "recorded"), ("org_hq", "org hq"), ("parent", "parent"), ("country", "country")],
                db_default="",
                default="",
                max_length=16,
            ),
        ),
    ]
