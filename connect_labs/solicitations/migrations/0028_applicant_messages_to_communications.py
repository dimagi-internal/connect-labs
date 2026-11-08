"""Applicant messages move into communications: each becomes a SentMessage about its round."""

from django.db import migrations


def forward(apps, schema_editor):
    ApplicantMessage = apps.get_model("solicitations", "ApplicantMessage")
    SentMessage = apps.get_model("communications", "SentMessage")
    ContentType = apps.get_model("contenttypes", "ContentType")
    if not ApplicantMessage.objects.exists():
        return
    round_type, _ = ContentType.objects.get_or_create(app_label="solicitations", model="solicitation")
    for message in ApplicantMessage.objects.all().iterator():
        row = SentMessage.objects.create(
            kind="solicitations.round_applicants",
            about_type=round_type,
            about_id=str(message.solicitation_id),
            org_id=message.org_id,
            to_email=message.to_email,
            to_name=message.to_name,
            reply_to=message.reply_to,
            subject=message.subject,
            body=message.body,
            delivery="queued" if message.queued else "not_sent",
            effects={"invite_id": message.invite_id} if message.invite_id else {},
            sent_by_id=message.sent_by_id,
        )
        # auto_now_add set it to now: keep when it was really sent.
        SentMessage.objects.filter(pk=row.pk).update(sent_at=message.sent_at)


class Migration(migrations.Migration):

    dependencies = [
        ("solicitations", "0027_applicant_messages"),
        ("communications", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop, hints={"run_on_secondary": False}),
        migrations.DeleteModel(name="ApplicantMessage"),
    ]
