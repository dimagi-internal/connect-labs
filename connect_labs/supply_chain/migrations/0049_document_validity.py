# A document's validity window: a program's duty exemption holds for a period.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("supply_chain", "0048_operationcall_source_sent_on"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="valid_from",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="valid_until",
            field=models.DateField(blank=True, null=True),
        ),
    ]
