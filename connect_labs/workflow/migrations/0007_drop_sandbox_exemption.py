from django.db import migrations


class Migration(migrations.Migration):
    """No workflow is kept out of the sandbox frame (2026-10-13 ruling): the recorded
    exemptions of 0006 are gone, and so is their table."""

    dependencies = [
        ("workflow", "0006_sandbox_exemption"),
    ]

    operations = [
        migrations.DeleteModel(name="SandboxExemption"),
    ]
