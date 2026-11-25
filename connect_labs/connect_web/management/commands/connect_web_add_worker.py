"""Register (or update) a Connect Web pool worker.

    manage.py connect_web_add_worker --phone +74260000001 --name "Test Worker 1"

The backup code and PIN are never taken on the command line (argv lands in shell history and the
process list). They come from the environment (CONNECT_WEB_BACKUP_CODE, CONNECT_WEB_PIN), else
from a hidden prompt, else — with --stdin — from the first one or two lines of standard input.
"""

from __future__ import annotations

import getpass
import os
import sys

from django.core.management.base import BaseCommand, CommandError

from connect_labs.connect_web.models import PoolWorker

BACKUP_CODE_ENV = "CONNECT_WEB_BACKUP_CODE"
PIN_ENV = "CONNECT_WEB_PIN"


class Command(BaseCommand):
    help = "Register or update a Connect Web pool worker (secrets via env, prompt or --stdin, never argv)."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="E.164 phone number, a +7426 demo number")
        parser.add_argument("--name", required=True, help="Display name")
        parser.add_argument("--username", default="", help="PersonalID username, if already known")
        parser.add_argument("--stdin", action="store_true", help="Read the backup code (line 1) and PIN (line 2)")
        parser.add_argument("--inactive", action="store_true", help="Register as inactive")

    def _secrets(self, options) -> tuple[str, str]:
        code, pin = os.environ.get(BACKUP_CODE_ENV, ""), os.environ.get(PIN_ENV, "")
        if options["stdin"]:
            lines = sys.stdin.read().splitlines()
            code = code or (lines[0].strip() if lines else "")
            pin = pin or (lines[1].strip() if len(lines) > 1 else "")
        elif not code:
            code = getpass.getpass("Backup code: ").strip()
            pin = pin or getpass.getpass("PIN (optional, Enter to skip): ").strip()
        return code, pin

    def handle(self, *args, **options):
        phone = options["phone"].strip()
        if not phone.startswith("+"):
            raise CommandError("--phone must be in E.164 form, e.g. +74260000001")
        if not phone.startswith("+7426"):
            self.stderr.write(self.style.WARNING("Pool workers are normally +7426 demo numbers (no OTP)."))
        code, pin = self._secrets(options)
        existing = PoolWorker.objects.filter(phone_number=phone).first()
        if not code and not existing:
            raise CommandError(f"No backup code: set {BACKUP_CODE_ENV}, answer the prompt, or pass --stdin.")

        worker = existing or PoolWorker(phone_number=phone)
        worker.display_name = options["name"].strip()
        worker.active = not options["inactive"]
        if options["username"]:
            worker.personalid_username = options["username"].strip()
        if code:
            worker.backup_code = code
            worker.sign_in_blocked_reason = ""
        if pin:
            worker.pin = pin
        worker.save()
        verb = "Updated" if existing else "Registered"
        self.stdout.write(self.style.SUCCESS(f"{verb} pool worker {worker.pk}: {worker.display_name} ({phone})"))
