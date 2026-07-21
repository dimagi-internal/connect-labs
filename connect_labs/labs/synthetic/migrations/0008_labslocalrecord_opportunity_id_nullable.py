from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("synthetic", "0007_syntheticopportunity_cloned_from_opportunity_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="labslocalrecord",
            name="opportunity_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
