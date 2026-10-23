"""Whether canopy may act as the visitor at labs' MCP (the jwt-bearer grant) becomes a
setting on Labs Admin › Canopy agents. It was the ``CANOPY_CLIENT_ID`` environment
variable, set on production's web tasks; the client id is now derived from
``CANOPY_BASE_URL``.

Existing rows -- history included, since a restore puts an old row's values back in
force -- get the switch on where the grant could have been on: ``CANOPY_CLIENT_ID`` set,
or (because this migration runs on the new task definition, which no longer carries
``CANOPY_CLIENT_ID``) a deployment with a signing key, a canopy and a public origin, all
three of which the grant needs. A new row's switch is off until staff tick it."""

import os

from django.conf import settings
from django.db import migrations, models


def _grant_was_possible() -> bool:
    if os.environ.get("CANOPY_CLIENT_ID"):
        return True
    return bool(
        getattr(settings, "CANOPY_SIGNING_KEY", "")
        and getattr(settings, "CANOPY_BASE_URL", "")
        and getattr(settings, "LABS_PUBLIC_URL", "")
    )


def forwards(apps, schema_editor):
    if _grant_was_possible():
        apps.get_model("labs", "CanopyAgentSettings").objects.update(acts_as_visitor=True)


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0034_canopy_runner_requirement_sensitive_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="canopyagentsettings",
            name="acts_as_visitor",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
