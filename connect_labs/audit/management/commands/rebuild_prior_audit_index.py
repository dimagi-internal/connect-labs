"""Rebuild the prior-audit projection for one opportunity, or verify it.

    manage.py rebuild_prior_audit_index --opportunity 1973
    manage.py rebuild_prior_audit_index --opportunity 1973 --verify-only
    manage.py rebuild_prior_audit_index --opportunity 1973 --json

``--verify-only`` is the gate: it diffs the projection against the live
computation and exits non-zero when they disagree, so it can block the
switch-over rather than being read by eye.

AUTHENTICATION: prefer ``--as <username>``, which looks up that labs user's
stored Connect token the way the reconcile command and Pulse's ingest do.
``--token`` still exists for local one-offs, but do NOT pass it through
run-labs-command -- that workflow takes the command line as a free-text
dispatch input, so the token would be recorded in the workflow inputs and the
Actions log. ``--as`` keeps the credential in the database where it already
lives.

Scope follows whichever identity is used: the export API returns what that
user's org membership can see. That is safe by construction here --
rebuild_opportunity merges and cannot delete rows for sessions it did not see
(#1260) -- but a narrow identity will simply build less, so prefer the widest
one available.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess, build_prior_audit_index
from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token


class _Rollback(Exception):
    """Unwind the surrounding atomic block without surfacing as a failure.

    Used for both outcomes that must not persist: a build that disagreed with
    the live index, and a --verify-only dry run that agreed. Django rolls back
    on any exception leaving an atomic block, so this is the mechanism; the
    caller catches it and reports normally.
    """


class Command(BaseCommand):
    help = "Rebuild (or verify) the local prior-audit projection for an opportunity."

    def add_arguments(self, parser):
        parser.add_argument("--opportunity", type=int, required=True, help="opportunity id")
        parser.add_argument(
            "--as", dest="username", default=None, help="labs username whose stored Connect token to use"
        )
        parser.add_argument(
            "--token", default=None, help="raw OAuth token (local use only — never via run-labs-command)"
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="do not write; diff the existing projection against the live computation",
        )
        parser.add_argument("--json", action="store_true", help="machine-readable output")

    def handle(self, *args, **opts):
        opp = opts["opportunity"]
        token = opts["token"]
        if not token:
            if not opts["username"]:
                raise CommandError("pass --as <username> (preferred) or --token")
            try:
                user = get_user_model().objects.get(username=opts["username"])
            except get_user_model().DoesNotExist:
                raise CommandError(
                    f"user {opts['username']!r} does not exist in labs "
                    "(they must have logged into labs in a browser at least once)"
                )
            try:
                token = get_valid_access_token(user)
            except ConnectTokenError as exc:
                # An expired refresh looks exactly like "nothing to build" if it
                # is allowed to fall through, so it has to stop the run.
                raise CommandError(f"no usable Connect token for {opts['username']!r}: {exc}") from exc
        data_access = AuditDataAccess(opportunity_id=opp, access_token=token)

        # One fetch, used for BOTH the rebuild and the live comparison. Fetching
        # twice would let the source change between them and turn an ordinary
        # race into what looks like a projection bug.
        sessions = [s for s in data_access.get_audit_sessions(status="completed") if s.opportunity_id == opp]
        live = build_prior_audit_index(sessions)

        # Build and verify inside ONE transaction, so a build that fails its own
        # gate leaves nothing behind.
        #
        # rebuild_opportunity is itself atomic and writes the
        # PriorAuditProjectionState row -- and that row is what flips
        # is_built(), so the instant it commits, every auditor on this
        # opportunity is served the projection instead of the live computation.
        # Verifying afterwards meant the command could print "do NOT switch the
        # read path" about a switch it had already made. A gate that fires after
        # the irreversible step is not a gate.
        #
        # It matters more here than for a typical cache: a wrong projection does
        # not raise, it renders as "this image was never audited".
        result = None
        agreed = False
        try:
            with transaction.atomic():
                # ALWAYS build, in both modes. --verify-only differs only in
                # rolling back afterwards -- that is what makes it a dry run
                # rather than a diff against an empty table, which could only
                # ever report every key as missing.
                result = projection.rebuild_opportunity(opp, sessions, built_by=opts.get("username") or "")

                # Judged against what THIS token can see. The projection may
                # legitimately hold rows from sessions outside this scope -- it
                # merges across identities (#1260) -- and calling those drift
                # would make the gate unusable for anyone without full
                # visibility.
                verdict = projection.verify_opportunity(opp, live, visible_session_ids={s.id for s in sessions})
                agreed = verdict.agrees
                if not agreed:
                    raise _Rollback
                # --verify-only is a genuine DRY RUN: it builds, checks whether
                # the build WOULD agree, and always rolls back. Without this it
                # could only diff an empty table against live and report every
                # key as missing, which answered nothing.
                if opts["verify_only"]:
                    raise _Rollback
        except _Rollback:
            pass

        committed = agreed and not opts["verify_only"]

        if opts["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "agrees": verdict.agrees,
                        "committed": committed,
                        "rebuild": result,
                        "live_keys": verdict.live_keys,
                        "projected_keys": verdict.projected_keys,
                        "beyond_scope": verdict.beyond_scope,
                        "missing": verdict.missing[:50],
                        "extra": verdict.extra[:50],
                        "mismatched": verdict.mismatched[:50],
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            if result:
                self.stdout.write(
                    f"opp {opp}: {result['sessions_contributing']}/{result['sessions_seen']} sessions "
                    f"contributed, {result['rows_written']} rows ({result['rows_total']} total)"
                )
            self.stdout.write(verdict.summary())
            # Say plainly whether anything persisted. The whole point of the
            # rollback is that "it ran" and "it took effect" are now different
            # outcomes, and the operator must not have to infer which happened.
            if committed:
                self.stdout.write(
                    f"COMMITTED — opp {opp} is now built, and the bulk-data page will read the projection."
                )
            elif opts["verify_only"]:
                self.stdout.write(
                    "DRY RUN — rolled back. "
                    + (
                        "A real build would agree; re-run without --verify-only to commit it."
                        if agreed
                        else "A real build would NOT agree; nothing was written."
                    )
                )
            else:
                self.stdout.write("ROLLED BACK — the build disagreed with the live index; nothing was written.")
            # Truncated on purpose: a systemic disagreement produces thousands of
            # lines and the first few already say which way it is broken.
            for label, items in (("missing", verdict.missing), ("extra", verdict.extra)):
                for key in items[:10]:
                    self.stdout.write(f"  {label}: {key}")
                if len(items) > 10:
                    self.stdout.write(f"  ... and {len(items) - 10} more {label}")
            for m in verdict.mismatched[:10]:
                self.stdout.write(f"  mismatched: {m['key']} live={m['live']} projected={m['projected']}")

        if not verdict.agrees:
            raise CommandError("projection does NOT agree with the live index — nothing was committed")
