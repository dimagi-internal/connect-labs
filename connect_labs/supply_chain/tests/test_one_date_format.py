"""Round 2, "One date format": every date a person reads is "30 Oct 2026", never "2026-10-30".

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import re
from pathlib import Path

from connect_labs.supply_chain.templatetags.supply_chain_extras import _fact_text, check_age, day

DATE_KEYS = (
    "since",
    "occurred_on",
    "resolved_on",
    "starts_on",
    "ended_on",
    "expected_on",
    "dispatched_on",
    "paid_on",
    "reviewed_on",
    "expires_on",
    "expires_at",
    "sent_on",
    "last_reminder_on",
    "added_on",
    "reported_on",
    "updated_on",
    "runs_dry_on",
    "source_on",
    "last_heard",
    "last_activity",
)


def test_no_supply_template_prints_an_iso_date_for_a_date_it_names():
    root = Path(__file__).resolve().parents[2] / "templates" / "supply_chain"
    offenders = []
    for path in sorted(root.rglob("*.html")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "value=" in line:
                continue
            for key in DATE_KEYS:
                for match in re.finditer(r"\{\{\s*[\w.]*\b" + key + r"\b([^}]*)\}\}", line):
                    if "|day" not in match.group(0) and "|date:" not in match.group(0):
                        offenders.append(f"{path.name}:{number}: {match.group(0)}")
    assert not offenders, "print these with `|day`: " + "; ".join(offenders)


def test_an_iso_date_in_a_fact_or_an_age_is_written_the_one_way():
    assert _fact_text("2026-07-08") == "8 Jul 2026"
    assert day("2026-10-30") == "30 Oct 2026"
    check = {"kind": "shipment_overdue", "days_open": 19, "since": "2026-09-05"}
    assert check_age(check) == "19 days past the expected date, 5 Sep 2026"
