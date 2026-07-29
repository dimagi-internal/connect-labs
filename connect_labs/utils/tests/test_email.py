"""
Tests for labs outbound email (#1039).

The behaviour worth pinning down here is the honesty of the not-configured
path. Django's console backend reports ``len(messages)`` sent for mail it wrote
to stdout and threw away, which is what let labs look like it could send email
when it could not. These tests assert the replacement reports 0.
"""

import logging
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.mail import EmailMessage, send_mail

from connect_labs.utils import email as labs_email
from connect_labs.utils import tasks as tasks_module
from connect_labs.utils.tasks import send_mail_async

NOT_CONFIGURED = "connect_labs.utils.email.NotConfiguredEmailBackend"


def _message(subject="Subject", to=None):
    return EmailMessage(subject=subject, body="body", from_email="noreply@example.com", to=to or ["a@example.com"])


class TestNotConfiguredEmailBackend:
    def test_reports_zero_sent_not_success(self, settings):
        """The regression that made #1039 dangerous: 'sent' for discarded mail."""
        settings.EMAIL_BACKEND = NOT_CONFIGURED
        assert send_mail("Subject", "body", "noreply@example.com", ["a@example.com"]) == 0

    def test_console_backend_would_have_claimed_success(self, settings):
        """Contrast case, documenting exactly what we moved away from."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
        assert send_mail("Subject", "body", "noreply@example.com", ["a@example.com"]) == 1

    def test_logs_a_warning_naming_subject_and_recipients(self, settings, caplog):
        settings.EMAIL_BACKEND = NOT_CONFIGURED
        with caplog.at_level(logging.WARNING, logger="connect_labs.utils.email"):
            mail.get_connection().send_messages([_message(subject="Invoice ready", to=["flw@example.com"])])

        assert len(caplog.records) == 1
        record = caplog.records[0].getMessage()
        assert "Email NOT sent" in record
        assert "Invoice ready" in record
        assert "flw@example.com" in record

    def test_empty_message_list_is_a_noop(self, settings):
        settings.EMAIL_BACKEND = NOT_CONFIGURED
        assert mail.get_connection().send_messages([]) == 0


class TestCheckEmailConfig:
    def test_disabled_environment_reports_that_mail_is_not_delivered(self, settings):
        settings.LABS_EMAIL_ENABLED = False
        problems = labs_email.check_email_config()
        assert len(problems) == 1
        assert "will NOT be delivered" in problems[0]

    def test_enabled_without_domain_is_a_problem(self, settings):
        settings.LABS_EMAIL_ENABLED = True
        settings.LABS_EMAIL_DOMAIN = ""
        assert any("LABS_EMAIL_DOMAIN is empty" in p for p in labs_email.check_email_config())

    def test_catches_sender_on_an_unverified_domain(self, settings):
        """The inherited default sends from a domain labs does not control."""
        settings.LABS_EMAIL_ENABLED = True
        settings.LABS_EMAIL_DOMAIN = "mail.labs.connect.dimagi.com"
        settings.DEFAULT_FROM_EMAIL = "Connect <noreply@commcare-connect.org>"
        settings.EMAIL_BACKEND = "anymail.backends.amazon_ses.EmailBackend"

        problems = labs_email.check_email_config()
        assert any("commcare-connect.org" in p and "not the SES-verified" in p for p in problems)

    def test_clean_when_fully_configured(self, settings):
        settings.LABS_EMAIL_ENABLED = True
        settings.LABS_EMAIL_DOMAIN = "mail.labs.connect.dimagi.com"
        settings.DEFAULT_FROM_EMAIL = "Connect Labs <noreply@mail.labs.connect.dimagi.com>"
        settings.EMAIL_BACKEND = "anymail.backends.amazon_ses.EmailBackend"

        assert labs_email.check_email_config() == []

    def test_subdomain_of_the_verified_domain_is_allowed(self, settings):
        """SES domain verification covers subdomains of the verified domain."""
        settings.LABS_EMAIL_ENABLED = True
        settings.LABS_EMAIL_DOMAIN = "labs.connect.dimagi.com"
        settings.DEFAULT_FROM_EMAIL = "noreply@mail.labs.connect.dimagi.com"
        settings.EMAIL_BACKEND = "anymail.backends.amazon_ses.EmailBackend"

        assert labs_email.check_email_config() == []


class TestSystemCheck:
    def test_silent_when_delivery_is_disabled(self, settings):
        """Local dev and CI live here; the check must not add noise."""
        settings.LABS_EMAIL_ENABLED = False
        assert labs_email.check_labs_email(app_configs=None) == []

    def test_errors_when_enabled_but_misconfigured(self, settings):
        settings.LABS_EMAIL_ENABLED = True
        settings.LABS_EMAIL_DOMAIN = ""
        settings.DEFAULT_FROM_EMAIL = "noreply@commcare-connect.org"

        errors = labs_email.check_labs_email(app_configs=None)
        assert errors
        assert all(e.id == "connect_labs.E001" for e in errors)


class TestSendLabsEmail:
    def test_queues_to_celery_rather_than_sending_inline(self, settings):
        """SES latency must never land on the request path (#1037)."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        mail.outbox = []

        with patch("connect_labs.utils.tasks.send_mail_async.delay") as delay:
            labs_email.send_labs_email("Subject", "body", ["a@example.com"])

        delay.assert_called_once()
        assert delay.call_args.kwargs["recipient_list"] == ["a@example.com"]
        assert mail.outbox == [], "message was sent inline instead of queued"

    def test_drops_empty_recipients_without_queueing(self):
        with patch("connect_labs.utils.tasks.send_mail_async.delay") as delay:
            assert labs_email.send_labs_email("Subject", "body", [None, ""]) is None
            assert labs_email.send_labs_email("Subject", "body", None) is None
        delay.assert_not_called()

    def test_filters_falsy_recipients_but_keeps_the_rest(self):
        with patch("connect_labs.utils.tasks.send_mail_async.delay") as delay:
            labs_email.send_labs_email("Subject", "body", ["", "a@example.com", None])
        assert delay.call_args.kwargs["recipient_list"] == ["a@example.com"]


class TestSendMailAsync:
    def test_resolves_sender_at_call_time_not_import_time(self, settings):
        """
        The old signature froze settings.DEFAULT_FROM_EMAIL at import, so a
        per-environment sender could never take effect in a warm worker.
        """
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        settings.DEFAULT_FROM_EMAIL = "Labs <noreply@mail.labs.connect.dimagi.com>"
        mail.outbox = []

        send_mail_async(subject="Subject", message="body", recipient_list=["a@example.com"])

        assert len(mail.outbox) == 1
        assert mail.outbox[0].from_email == "Labs <noreply@mail.labs.connect.dimagi.com>"

    def test_explicit_sender_wins(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        settings.DEFAULT_FROM_EMAIL = "Labs <noreply@mail.labs.connect.dimagi.com>"
        mail.outbox = []

        send_mail_async(
            subject="Subject",
            message="body",
            recipient_list=["a@example.com"],
            from_email="alerts@mail.labs.connect.dimagi.com",
        )

        assert mail.outbox[0].from_email == "alerts@mail.labs.connect.dimagi.com"

    def test_zero_sent_is_logged_and_not_retried(self, settings, caplog):
        settings.EMAIL_BACKEND = NOT_CONFIGURED
        with caplog.at_level(logging.WARNING, logger="connect_labs.utils.tasks"):
            result = send_mail_async(subject="Subject", message="body", recipient_list=["a@example.com"])

        assert result == 0
        assert any("0 messages sent" in r.getMessage() for r in caplog.records)

    def test_transient_failure_is_retried_then_surfaces(self, settings):
        """
        SES throttles labs at 1 msg/sec, so a transient failure must be retried
        rather than dropped — but it must still end in FAILURE, not silence.
        """
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

        with patch("connect_labs.utils.tasks.send_mail", side_effect=RuntimeError("SES throttled")) as send:
            result = send_mail_async.apply(
                kwargs=dict(subject="Subject", message="body", recipient_list=["a@example.com"])
            )

        assert send.call_count == tasks_module.MAX_RETRIES + 1, "expected one initial attempt plus every retry"
        assert result.state == "FAILURE"
        assert isinstance(result.result, RuntimeError)

    def test_direct_call_propagates_the_error(self, settings):
        """Called outside a worker, celery re-raises rather than scheduling."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        with patch("connect_labs.utils.tasks.send_mail", side_effect=RuntimeError("SES throttled")):
            with pytest.raises(RuntimeError, match="SES throttled"):
                send_mail_async(subject="Subject", message="body", recipient_list=["a@example.com"])


class TestSettingsWiring:
    """
    Guards the labs_aws SES wiring without importing that settings module (it
    requires AWS-only env). Asserts the contract the module depends on.
    """

    def test_anymail_ses_backend_is_importable_and_uses_sesv2(self):
        from anymail.backends.amazon_ses import EmailBackend

        # If anymail ever moves off sesv2, the IAM policy in infra/labs-email.yml
        # and the AMAZON_SES_CONFIGURATION_SET_NAME key both need revisiting.
        assert hasattr(EmailBackend, "open")
        import inspect

        assert "sesv2" in inspect.getsource(EmailBackend.open)

    def test_configuration_set_setting_name_matches_anymail(self):
        import inspect

        from anymail.backends.amazon_ses import EmailBackend

        source = inspect.getsource(EmailBackend.__init__)
        assert "configuration_set_name" in source
