"""The canopy live probe's service account (``connect_labs.labs.canopy.PROBE_USERNAME``).

canopy asks ``/labs/canopy/probe/`` for a real ID-JAG naming this account, redeems
it at ``/o/token/`` and makes one read-only MCP call with it — the whole grant
chain, on a schedule, with no visitor. So it needs a principal that is not a
person: active (the SDK refuses an inactive subject), with no usable password,
no staff or superuser bit, no email and no PAT. The username has a ``:``, which
no Connect username can, and the OAuth callback refuses it by name besides.

A migration rather than a command because the deploy runs ``migrate`` whenever a
migration file changes: the account exists the moment the code that names it
does. Idempotent — an existing row is brought back to this shape, never left
with a password or a staff bit. Not reversed: deleting it would take its MCP
audit rows' attribution with it, and a row that exists does nothing on its own
(the probe is still off until ``CANOPY_CLIENT_ID`` turns the grant on).
"""

from django.contrib.auth.hashers import make_password
from django.db import migrations

# Literal, not imported: a migration must keep meaning what it meant when it ran.
PROBE_USERNAME = "canopy:probe"
PROBE_DISPLAY_NAME = "canopy live probe (service account)"


def create_probe_user(apps, schema_editor):
    User = apps.get_model("users", "User")
    user, _ = User.objects.get_or_create(username=PROBE_USERNAME)
    user.name = PROBE_DISPLAY_NAME
    user.email = None
    user.password = make_password(None)  # unusable: no password login, ever
    user.is_active = True
    user.is_staff = False
    user.is_superuser = False
    user.save()
    user.groups.clear()
    user.user_permissions.clear()


class Migration(migrations.Migration):
    dependencies = [
        ("mcp", "0005_drop_delegated_grant_tables_moved_to_canopy_sdk"),
        ("users", "0024_user_view_synthetic_opps"),
    ]

    operations = [
        migrations.RunPython(create_probe_user, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
