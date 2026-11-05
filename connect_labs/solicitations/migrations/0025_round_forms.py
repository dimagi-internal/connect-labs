"""A directory round can run several forms; a submission records which one.

Existing imported submissions all came from their round's only form, whose key
is the round's own slug, so that is what they are backfilled with. Web
submissions (no source row) keep a blank form.
"""

from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_source_form(apps, schema_editor):
    Response = apps.get_model("solicitations", "SolicitationResponse")
    Solicitation = apps.get_model("solicitations", "Solicitation")
    slug = Subquery(Solicitation.objects.filter(pk=OuterRef("solicitation_id")).values("slug")[:1])
    Response.objects.filter(source_row__isnull=False).update(source_form=slug)


class Migration(migrations.Migration):
    dependencies = [
        ("solicitations", "0024_service_areas"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitation",
            name="forms",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="solicitationresponse",
            name="source_form",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.RunPython(backfill_source_form, migrations.RunPython.noop, hints={"run_on_secondary": False}),
        migrations.RemoveConstraint(
            model_name="solicitationresponse",
            name="uniq_response_per_source_row",
        ),
        migrations.AddConstraint(
            model_name="solicitationresponse",
            constraint=models.UniqueConstraint(
                fields=("solicitation", "source_form", "source_row"), name="uniq_response_per_form_row"
            ),
        ),
    ]
