from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("microplans", "0005_simple_microplan"),
    ]

    operations = [
        migrations.AddField(
            model_name="simplemicroplan",
            name="program_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="simplemicroplan",
            name="team",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
