from django.db import migrations, models


class Migration(migrations.Migration):
    """Whether, when and through which chatbot an update link was sent to its recipient's PersonalID chat."""

    dependencies = [
        ("supply_chain", "0058_update_link_recipient"),
    ]

    operations = [
        migrations.AddField(
            model_name="updatelink",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="updatelink",
            name="sent_via_bot",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.AddField(
            model_name="updatelink",
            name="send_error",
            field=models.CharField(blank=True, db_default="", default="", max_length=255),
        ),
    ]
