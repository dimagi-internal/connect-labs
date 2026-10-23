"""The canopy workspace (tenant) labs' site is registered in, kept on Labs Admin › Canopy
agents and sent as ``CANOPY_HOST["WORKSPACE"]`` (dimagi-canopy 0.16), so a deleted or
moved default agent cannot break canopy's list.

Existing rows are left blank: the workspace is canopy's fact, not one labs can know here.
While it is blank the default agent names the tenant exactly as before; the first answer
canopy gives fills it in (``canopy_settings.learn_workspace``), and every save records it."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0035_canopy_agent_settings_acts_as_visitor"),
    ]

    operations = [
        migrations.AddField(
            model_name="canopyagentsettings",
            name="workspace",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
