from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("canopy_bridge", "0003_erasure"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pendingturn",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("delivered", "Delivered"),
                    ("failed", "Failed"),
                    ("timed_out", "Timed Out"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="pending",
                max_length=10,
            ),
        ),
    ]
