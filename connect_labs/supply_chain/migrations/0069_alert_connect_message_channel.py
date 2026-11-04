from django.db import migrations, models


def whatsapp_to_connect_message(apps, schema_editor):
    """Alerts set up for WhatsApp become Connect-message alerts.

    Their number stays in the Connect username box, so it is visible on the alert and a person can replace it
    with the right username; until then Open Chat Studio refuses it and the log says so. Nothing was ever
    sent over WhatsApp (every notice for it was logged `no_channel`), so no history changes.
    """
    apps.get_model("supply_chain", "AlertSubscription").objects.filter(channel="whatsapp").update(
        channel="connect_message"
    )


class Migration(migrations.Migration):

    dependencies = [("supply_chain", "0068_partner_invoices")]

    operations = [
        migrations.RemoveConstraint(model_name="alertsubscription", name="alert_subscription_has_one_recipient"),
        migrations.RenameField(
            model_name="alertsubscription", old_name="recipient_phone", new_name="recipient_connect_username"
        ),
        migrations.AlterField(
            model_name="alertsubscription",
            name="recipient_connect_username",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="alertsubscription",
            name="channel",
            field=models.CharField(
                choices=[("email", "email"), ("connect_message", "connect message"), ("whatsapp", "whatsapp")],
                default="email",
                max_length=16,
            ),
        ),
        migrations.RunPython(
            whatsapp_to_connect_message, migrations.RunPython.noop, hints={"run_on_secondary": False}
        ),
        migrations.AlterField(
            model_name="alertsubscription",
            name="channel",
            field=models.CharField(
                choices=[("email", "email"), ("connect_message", "connect message")], default="email", max_length=16
            ),
        ),
        migrations.AddField(
            model_name="alertnotice",
            name="delivery_note",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AlterField(
            model_name="alertnotice",
            name="delivery",
            field=models.CharField(
                choices=[
                    ("pending", "pending"),
                    ("queued", "queued"),
                    ("email_disabled", "email disabled"),
                    ("no_address", "no address"),
                    ("no_channel", "no channel"),
                    ("no_connection", "no connection"),
                    ("no_chatbot", "no chatbot"),
                    ("refused", "refused"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="alertsubscription",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("recipient_user__isnull", False), ("recipient_email", ""), ("recipient_connect_username", "")
                    ),
                    models.Q(
                        ("recipient_user__isnull", True),
                        models.Q(
                            models.Q(
                                models.Q(("recipient_email", ""), _negated=True), ("recipient_connect_username", "")
                            ),
                            models.Q(
                                ("recipient_email", ""), models.Q(("recipient_connect_username", ""), _negated=True)
                            ),
                            _connector="OR",
                        ),
                    ),
                    _connector="OR",
                ),
                name="alert_subscription_has_one_recipient",
            ),
        ),
    ]
