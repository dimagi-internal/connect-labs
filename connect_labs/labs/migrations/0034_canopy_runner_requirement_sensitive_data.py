"""canopy renamed the runner flag real data needs: ``zdr`` is now ``sensitive_data``
(canopy-web spec 2026-10-22-sensitive-data-agents-design; dimagi-canopy 0.15). canopy
no longer accepts the old token, so every row -- history included, since a restore puts
an old row's values back in force -- names the new one."""

from django.db import migrations

OLD, NEW = "zdr", "sensitive_data"


def _swap(values, old, new) -> list:
    out = []
    for value in values or []:
        value = new if value == old else value
        if value not in out:
            out.append(value)
    return out


def _rewrite(apps, old, new):
    Settings = apps.get_model("labs", "CanopyAgentSettings")
    for row in Settings.objects.all():
        if old in (row.runner_requirements or []):
            row.runner_requirements = _swap(row.runner_requirements, old, new)
            row.save(update_fields=["runner_requirements"])


def forwards(apps, schema_editor):
    _rewrite(apps, OLD, NEW)


def backwards(apps, schema_editor):
    _rewrite(apps, NEW, OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0033_canopy_agent_settings_from_site_agents"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
