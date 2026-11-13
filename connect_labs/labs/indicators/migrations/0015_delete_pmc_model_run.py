from django.db import migrations


class Migration(migrations.Migration):
    """Malaria PMC was retired on 13 Nov 2026; its live-run table goes with it."""

    dependencies = [("indicators", "0014_pmc_model_run")]

    operations = [migrations.DeleteModel(name="PmcModelRun")]
