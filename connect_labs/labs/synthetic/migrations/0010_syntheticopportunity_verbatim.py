from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("synthetic", "0009_syntheticopportunity_generated_folder_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="syntheticopportunity",
            name="verbatim_paths",
            field=ArrayField(
                base_field=models.CharField(max_length=300),
                blank=True,
                default=list,
                help_text="form_json paths whose values were copied VERBATIM from cloned_from_opportunity_id (connect-labs#2150). Non-empty means this opp serves REAL values: it is never generated data, and only its creator and the people in allowed_emails may see it, whatever allowed_domains says. Set only by the clone flow: see connect_labs/labs/synthetic/verbatim.py.",
                size=None,
            ),
        ),
        migrations.AddField(
            model_name="syntheticopportunity",
            name="allowed_emails",
            field=ArrayField(
                base_field=models.CharField(max_length=254),
                blank=True,
                default=list,
                help_text="Individual addresses (besides the creator) who may see an opp carrying verbatim values. Each was checked, when added, to read the source opportunity's raw visits with their own token. Ignored when verbatim_paths is empty.",
                size=None,
            ),
        ),
    ]
