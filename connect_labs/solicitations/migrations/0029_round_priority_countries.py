from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("solicitations", "0028_applicant_messages_to_communications"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitation",
            name="priority_countries",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
