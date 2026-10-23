"""Labs Admin › Canopy agents' "Real data" tick is now "Sensitive data": each agent's
``real_data`` key is ``sensitive_data`` (dimagi-canopy 0.17 renames site.json's
``real_data`` the same way). Every row -- history included, since a restore puts an
old row's ticks back in force -- carries the new key."""

from django.db import migrations

OLD, NEW = "real_data", "sensitive_data"


def _renamed(ticks, old, new) -> list:
    out = []
    for tick in ticks or []:
        if isinstance(tick, dict) and old in tick:
            tick = {(new if k == old else k): v for k, v in tick.items()}
        out.append(tick)
    return out


def _rewrite(apps, old, new):
    Settings = apps.get_model("labs", "CanopyAgentSettings")
    for row in Settings.objects.all():
        agents = _renamed(row.agents, old, new)
        if agents != (row.agents or []):
            row.agents = agents
            row.save(update_fields=["agents"])


def forwards(apps, schema_editor):
    _rewrite(apps, OLD, NEW)


def backwards(apps, schema_editor):
    _rewrite(apps, NEW, OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0036_canopy_agent_settings_workspace"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
