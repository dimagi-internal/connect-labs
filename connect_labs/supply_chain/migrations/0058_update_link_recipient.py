from django.db import migrations, models


class Migration(migrations.Migration):
    """An update link can name the PersonalID user it was sent to.

    Not authentication (the link still works for whoever holds it): it is who
    the link was sent to, so what is recorded through it can be attributed.
    Existing links and submissions have no recipient.
    """

    dependencies = [
        ("supply_chain", "0057_revoke_links_to_running_organisations"),
    ]

    operations = [
        migrations.AddField(
            model_name="updatelink",
            name="connect_username",
            field=models.CharField(blank=True, db_index=True, default="", max_length=150),
        ),
        migrations.AddField(
            model_name="updatelinksubmission",
            name="connect_username",
            field=models.CharField(blank=True, db_index=True, default="", max_length=150),
        ),
    ]
