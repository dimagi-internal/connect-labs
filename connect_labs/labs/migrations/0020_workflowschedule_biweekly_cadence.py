# Choices-only change: adds the "Every 2 weeks" cadence. No database column changes -
# Django records choices as field state, so the migration exists to keep makemigrations
# quiet and the field definition auditable, not because the table needs altering.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0019_doccomment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflowschedule",
            name="cadence",
            field=models.CharField(
                choices=[
                    ("daily", "Daily"),
                    ("weekdays", "Weekdays (Mon–Fri)"),
                    ("weekly", "Weekly"),
                    ("biweekly", "Every 2 weeks"),
                    ("monthly", "Monthly"),
                ],
                max_length=16,
            ),
        ),
    ]
