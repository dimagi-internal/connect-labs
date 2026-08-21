"""Scheduled reconciliation for the prior-audit projection.

Thin on purpose: the work, and every judgement in it, lives in the
``reconcile_prior_audit_index`` command so it is runnable by hand with the same
semantics. A task that reimplemented the logic would drift from the command that
operators actually use during an incident.

Report-only by default. Automatic repair is deliberately NOT the default: a
repair rebuilds from whatever the configured identity can see, and if that scope
ever narrows, an unattended repair would delete real prior verdicts on a
schedule. The command refuses that case, but defaulting to report-only means a
human sees the drift before anything is rewritten.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)

#: Labs username whose Connect token reconciliation runs under. No default --
#: see reconcile_prior_audit_index for why guessing at scope is unsafe.
RECONCILE_USER_SETTING = "PRIOR_AUDIT_RECONCILE_USERNAME"


@shared_task(name="connect_labs.audit.reconcile_prior_audit_index")
def reconcile_prior_audit_index(repair: bool = False) -> str:
    username = getattr(settings, RECONCILE_USER_SETTING, "") or ""
    if not username:
        # Not an exception: an unset identity is a configuration state, not a
        # failure, and raising here would page on every beat tick in an
        # environment that has simply not opted in.
        logger.info(
            "prior-audit reconciliation skipped: %s is unset. Set it to a labs username "
            "whose Connect org membership covers every audited opportunity.",
            RECONCILE_USER_SETTING,
        )
        return "skipped: no reconcile user configured"

    args = ["reconcile_prior_audit_index", f"--as={username}"]
    if repair:
        args.append("--repair")
    try:
        call_command(*args)
        return "ok"
    except Exception as exc:
        # CommandError on drift is the expected signal, and it must be visible
        # in the worker log rather than swallowed -- drift that nobody sees is
        # the state this task exists to prevent.
        logger.warning("prior-audit reconciliation reported a problem: %s", exc)
        return f"drift: {exc}"
