"""Run another management command and print only its output SEALED to a key.

    python manage.py run_sealed --recipient <b64 X25519 pubkey> -- shell -c "print(1)"

The wrapped command's stdout and stderr (including a traceback if it raises) are
captured in memory, then printed as a sealed envelope that only the holder of
the recipient's private key can open (connect_labs/utils/sealed_output.py).
`run-labs-command.yml` uses this whenever a dispatcher passes `recipient`, so the
plaintext reaches neither the Actions log nor CloudWatch. Log records emitted by
Django's logging handlers still go to the container's stderr (CloudWatch, inside
AWS) as they always have; the workflow does not copy them to the Actions log.

The process exits with the wrapped command's exit code, so a failure still
fails the workflow.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import traceback

from django.core.management import BaseCommand, CommandError, call_command

from connect_labs.utils.sealed_output import SealedOutputError, envelope_lines, parse_recipient, seal


class Command(BaseCommand):
    help = "Run a management command and print its output sealed to --recipient."

    def add_arguments(self, parser):
        parser.add_argument("--recipient", required=True, help="base64 raw X25519 public key")
        parser.add_argument("wrapped", nargs=argparse.REMAINDER, help="-- <command> [args...]")

    def handle(self, *args, recipient, wrapped, **options):
        try:
            parse_recipient(recipient)
        except SealedOutputError as exc:
            raise CommandError(str(exc)) from exc
        if wrapped and wrapped[0] == "--":
            wrapped = wrapped[1:]
        if not wrapped:
            raise CommandError("nothing to run: pass -- <command> [args...]")
        if wrapped[0] == "run_sealed":
            raise CommandError("refusing to nest run_sealed")

        buf = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                call_command(wrapped[0], *wrapped[1:], stdout=buf, stderr=buf)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            except BaseException:  # noqa: BLE001 -- the traceback is output too, sealed with the rest
                traceback.print_exc(file=buf)
                code = 1

        text = buf.getvalue()
        blob = seal(text.encode(), recipient)
        out = sys.__stdout__ or sys.stdout
        for line in envelope_lines(blob, plaintext_bytes=len(text.encode()), plaintext_lines=text.count("\n")):
            out.write(line + "\n")
        out.write(f"run_sealed exit={code}\n")
        out.flush()
        if code:
            sys.exit(code)
