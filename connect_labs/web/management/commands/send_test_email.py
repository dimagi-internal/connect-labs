"""
Verify labs outbound email end to end.

Intended to be run from an ECS exec session against the real deployment, which
is the only place the SES identity, the task-role permission and the sandbox
status can all be exercised together:

    aws ecs execute-command --cluster labs-jj-cluster --task <id> \\
      --container web --interactive \\
      --command "python manage.py send_test_email --to you@dimagi.com --sync"

``--sync`` sends inline so failures surface in the session; without it the
message is queued to Celery, which is how application code should send.

Note the SES sandbox: until production access is granted, the *recipient* must
itself be a verified SES identity. A test to an unverified address failing with
"Email address is not verified" means the plumbing works and the sandbox is the
only thing left. See docs/OUTBOUND_EMAIL.md.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.utils.email import check_email_config, send_labs_email
from connect_labs.utils.tasks import send_mail_async


class Command(BaseCommand):
    help = "Send a test email to verify outbound email configuration."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient address.")
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Send inline instead of queueing to Celery, so errors surface here.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if the configuration preflight reports problems.",
        )

    def handle(self, *args, **options):
        recipient = options["to"]

        self.stdout.write("Outbound email configuration:")
        self.stdout.write(f"  EMAIL_BACKEND        {settings.EMAIL_BACKEND}")
        self.stdout.write(f"  LABS_EMAIL_ENABLED   {getattr(settings, 'LABS_EMAIL_ENABLED', False)}")
        self.stdout.write(f"  LABS_EMAIL_DOMAIN    {getattr(settings, 'LABS_EMAIL_DOMAIN', '') or '(unset)'}")
        self.stdout.write(f"  DEFAULT_FROM_EMAIL   {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(
            f"  SES configuration set {getattr(settings, 'LABS_SES_CONFIGURATION_SET', '(n/a)')} "
            f"in {getattr(settings, 'LABS_SES_REGION', '(n/a)')}"
        )

        problems = check_email_config()
        if problems:
            self.stdout.write(self.style.WARNING("\nPreflight found problems:"))
            for problem in problems:
                self.stdout.write(self.style.WARNING(f"  - {problem}"))
            if not options["force"]:
                raise CommandError("Refusing to send. Fix the above, or pass --force to send anyway.")
        else:
            self.stdout.write(self.style.SUCCESS("\nPreflight clean."))

        subject = "Connect Labs outbound email test"
        body = (
            "This is a test message from Connect Labs (labs.connect.dimagi.com).\n\n"
            f"Backend: {settings.EMAIL_BACKEND}\n"
            f"From:    {settings.DEFAULT_FROM_EMAIL}\n\n"
            "If you received this, labs can send email."
        )

        if options["sync"]:
            self.stdout.write(f"\nSending inline to {recipient}...")
            sent = send_mail_async(subject=subject, message=body, recipient_list=[recipient])
            if sent:
                self.stdout.write(self.style.SUCCESS(f"SES accepted the message ({sent} sent)."))
            else:
                raise CommandError("Backend reported 0 messages sent — nothing was delivered.")
        else:
            result = send_labs_email(subject=subject, message=body, recipient_list=[recipient])
            self.stdout.write(
                self.style.SUCCESS(f"\nQueued to Celery (task id {result.id}). Check the worker log for the outcome.")
            )
