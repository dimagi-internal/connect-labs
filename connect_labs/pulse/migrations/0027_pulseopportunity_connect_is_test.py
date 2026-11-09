from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pulse", "0026_pulsework_beneficiary_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="pulseopportunity",
            name="connect_is_test",
            field=models.BooleanField(default=False),
        ),
    ]
