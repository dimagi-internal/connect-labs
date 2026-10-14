# Team size becomes Connect's bracket ("11-50") instead of a headcount, so the
# directory's value and Connect's are the same kind of thing once an organisation
# is linked. The brackets are copied here rather than imported: a migration must
# keep meaning what it meant when it ran.

from django.db import migrations, models

BRACKETS = (("1-10", 10), ("11-50", 50), ("51-200", 200), ("201-500", 500))


def to_bracket(apps, schema_editor):
    OrgProfile = apps.get_model("marketplace", "OrgProfile")
    for profile in OrgProfile.objects.exclude(team_size=""):
        try:
            count = int(profile.team_size)
        except ValueError:
            continue
        bracket = "" if count < 1 else next((b for b, upper in BRACKETS if count <= upper), "500+")
        OrgProfile.objects.filter(pk=profile.pk).update(team_size=bracket)


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0007_orgmembership_granted_for_org_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orgprofile",
            name="team_size",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        # Irreversible: a bracket cannot be cast back to a headcount. The sheet
        # still holds the headcounts, so a rollback re-imports them.
        migrations.RunPython(to_bracket, hints={"run_on_secondary": False}),
    ]
