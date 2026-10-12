from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0025_userocstoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="labsorg",
            name="labs_only",
            field=models.BooleanField(db_default=False, db_index=True, default=False, editable=False),
        ),
    ]
