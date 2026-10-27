"""ACE may mint full-access MCP credentials for itself (Jonathan, 2026-10-27): it is never
in a project space with real data, so a passkey approval for each of its tokens would
guard nothing. Ticks ``FullAccessSelfMint`` for its account when that account exists;
the admin (Django admin › Full-access self-mints) changes it from then on."""
from django.conf import settings
from django.db import migrations

ACE_EMAIL = "ace@dimagi-ai.com"
REASON = "ACE: an agent account that is never in a project space with real data (Jonathan, 2026-10-27)"


def tick_ace(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    SelfMint = apps.get_model("mcp", "FullAccessSelfMint")
    for user in User.objects.filter(email__iexact=ACE_EMAIL):
        SelfMint.objects.get_or_create(user=user, defaults={"reason": REASON})


class Migration(migrations.Migration):
    dependencies = [
        ("mcp", "0016_full_access_approval"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(tick_ace, migrations.RunPython.noop, hints={"run_on_secondary": False})]
