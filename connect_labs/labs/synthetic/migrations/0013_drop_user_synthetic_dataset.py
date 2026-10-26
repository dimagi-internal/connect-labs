from django.db import migrations

#: The visit caches self-service data could have filled, all keyed per opportunity.
CACHES = ("RawVisitCache", "ComputedVisitCache", "ComputedEntityCache", "ComputedFLWCache")


def clear_mixed_caches(apps, schema_editor):
    """Self-service synthetic data was served under the REAL opportunity's id, so whatever
    it fetched sits in that opportunity's shared caches, mixed with real rows, for every
    holder. Empty those opportunities' caches before the datasets go: the next read
    reloads them from Connect."""
    dataset = apps.get_model("synthetic", "UserSyntheticDataset")
    opps = set(dataset.objects.values_list("opportunity_id", flat=True))
    if not opps:
        return
    for name in CACHES:
        apps.get_model("labs", name).objects.filter(opportunity_id__in=opps).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("synthetic", "0012_unmark_replayed_clones"),
        ("labs", "0040_labs_data_reader_login"),
    ]

    operations = [
        migrations.RunPython(clear_mixed_caches, migrations.RunPython.noop, hints={"run_on_secondary": False}),
        migrations.DeleteModel(
            name="UserSyntheticDataset",
        ),
    ]
