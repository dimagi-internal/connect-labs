from django.conf import settings
from django.db import migrations, models


def creators_manage(apps, schema_editor):
    """Every existing space: its creator is its manager (audience defaults to holders)."""
    DataSpace = apps.get_model("labsdata", "DataSpace")
    for space in DataSpace.objects.exclude(created_by__isnull=True):
        space.managers.add(space.created_by_id)


class Migration(migrations.Migration):

    dependencies = [
        ("labsdata", "0003_page_holds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="dataspace",
            name="audience",
            field=models.CharField(
                choices=[("holders", "Anyone with access to the sources"), ("private", "Only managers")],
                default="holders",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="dataspace",
            name="managers",
            field=models.ManyToManyField(blank=True, related_name="managed_data_spaces", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(creators_manage, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
