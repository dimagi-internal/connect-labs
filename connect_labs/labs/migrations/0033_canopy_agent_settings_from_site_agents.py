"""Labs Admin › Canopy agents becomes a table of ticks per agent (spec
2026-10-22-canopy-site-agents-api-design §3). Every row, history included, keeps its
meaning: the site agent becomes the default and is shown; the data agent and every
other offered agent are shown; an agent allowed over real data is shown with "Real data"
ticked."""

from django.db import migrations, models


def _ticks(row) -> list[dict]:
    phi = [a for a in (row.phi_agents or []) if a]
    slugs = [row.site_agent, row.data_agent, *(row.offered_agents or []), *phi]
    out, seen = [], set()
    for slug in slugs:
        if slug and slug not in seen:
            seen.add(slug)
            out.append({"slug": slug, "offered": True, "real_data": slug in phi})
    return out


def forwards(apps, schema_editor):
    Settings = apps.get_model("labs", "CanopyAgentSettings")
    for row in Settings.objects.all():
        row.agents = _ticks(row)
        row.default_agent = row.site_agent
        row.save(update_fields=["agents", "default_agent"])


def backwards(apps, schema_editor):
    Settings = apps.get_model("labs", "CanopyAgentSettings")
    for row in Settings.objects.all():
        ticks = [t for t in row.agents or [] if t.get("offered")]
        row.site_agent = row.default_agent
        row.phi_agents = [t["slug"] for t in ticks if t.get("real_data")]
        row.offered_agents = [t["slug"] for t in ticks if t["slug"] != row.default_agent]
        row.save(update_fields=["site_agent", "phi_agents", "offered_agents"])


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0032_canopy_agent_settings_offered_agents"),
    ]

    operations = [
        migrations.AddField(
            model_name="canopyagentsettings",
            name="agents",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="canopyagentsettings",
            name="default_agent",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="canopyagentsettings",
            name="site_agents_version",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="canopyagentsettings", name="site_agent"),
        migrations.RemoveField(model_name="canopyagentsettings", name="data_agent"),
        migrations.RemoveField(model_name="canopyagentsettings", name="phi_agents"),
        migrations.RemoveField(model_name="canopyagentsettings", name="offered_agents"),
    ]
