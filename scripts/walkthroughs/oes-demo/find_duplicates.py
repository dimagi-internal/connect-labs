"""Duplicates in the demo scopes, by the keys the seeder itself uses.

    make manage CMD="shell -c \\"exec(open('scripts/walkthroughs/oes-demo/find_duplicates.py').read())\\""

Reports by default. Removing anything takes `REMOVE = True` below, deliberately
edited by hand rather than passed as a flag, because this deletes supply rows
and the person doing it should have read what it found first.

## Why this exists

`seed_remote.py` used to create rather than find, so re-running it doubled the
rounds, the orders, the quotes and the awards. That is fixed -- every record
now keys on something the seed document supplies -- but the fix does not reach
backwards into an environment already seeded twice. This is how you tell
whether one was, and it is the same nine keys the seeder uses, so the two
cannot drift apart in their idea of what a duplicate is.

## What it will not touch

The LEDGER. Movements, stock counts and distributions are events, and two
identical receipts against one order are a real thing that can happen -- there
is nothing in the second one that says it is a mistake rather than a second
delivery. Deleting them by inspection would mean this script deciding which
real events are allowed to have happened. They are counted here and left
alone; if a ledger genuinely doubled, purge the scope and re-seed, which is
what `SupplyDataAccess.purge()` is for.

Update links are left alone too. A revoked or duplicated link is a token
somebody may have sent to a partner, and deleting it silently breaks a page
that partner has bookmarked.
"""

from collections import defaultdict

from connect_labs.supply_chain.models import (
    Award,
    Contract,
    Invoice,
    Movement,
    Payment,
    Quote,
    Receipt,
    StockCount,
    Supplier,
    SupplyPoint,
    Tender,
)

# The OES demo's four programmes. Edit if you are checking another environment.
SCOPES = [10610, 10671, 10672, 10673]

# Set True to delete the extras, keeping the LOWEST id in each group -- the
# first one written, which is the one anything else already points at.
REMOVE = False


def _scope_keys():
    return [f"prog:{program_id}" for program_id in SCOPES]


# Each entry is (what it is, the rows, the key that makes two of them the same).
# The keys mirror seed_remote.py exactly. A `<none:pk>` key means the row has
# no natural key at all, which makes it unique by definition rather than a
# duplicate -- a row nobody can identify is not one this script will delete.
CHECKS = [
    (
        "Tender by (programme, label)",
        lambda: Tender.objects.filter(program_id__in=SCOPES),
        lambda r: (r.program_id, r.label),
    ),
    (
        "Contract by reference",
        lambda: Contract.objects.filter(program_id__in=SCOPES),
        lambda r: (r.program_id, r.reference or f"<none:{r.pk}>"),
    ),
    (
        "Quote by (tender, supplier, item)",
        lambda: Quote.objects.filter(tender__program_id__in=SCOPES),
        lambda r: (r.tender_id, r.supplier_id, r.item_id),
    ),
    (
        "Award by tender",
        lambda: Award.objects.filter(tender__program_id__in=SCOPES),
        lambda r: (r.tender_id,),
    ),
    (
        "Invoice by contract",
        lambda: Invoice.objects.filter(contract__program_id__in=SCOPES),
        lambda r: (r.contract_id,),
    ),
    (
        "Receipt by (contract, reference)",
        lambda: Receipt.objects.filter(contract__program_id__in=SCOPES),
        lambda r: (r.contract_id, r.reference or f"<none:{r.pk}>"),
    ),
    (
        "Payment by reference",
        lambda: Payment.objects.filter(invoice__contract__program_id__in=SCOPES),
        lambda r: (r.reference or f"<none:{r.pk}>",),
    ),
    (
        "Supplier by (scope, name)",
        lambda: Supplier.objects.filter(scope_key__in=_scope_keys()),
        lambda r: (r.scope_key, (r.name or "").casefold()),
    ),
    (
        "Supply point by (programme, slug)",
        lambda: SupplyPoint.objects.filter(program_id__in=SCOPES),
        lambda r: (r.program_id, r.slug),
    ),
]


def duplicates(rows, key):
    """`{key: [ids]}` for every key held by more than one row, oldest first."""
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row.pk)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def report():
    total, found = 0, {}
    print(f"Demo scopes {SCOPES}\n")
    for label, rows, key in CHECKS:
        queryset = rows()
        dupes = duplicates(queryset, key)
        extra = sum(len(ids) - 1 for ids in dupes.values())
        total += extra
        found[label] = (rows, dupes)
        print(f"  {label:36} {queryset.count():4} rows   {f'{extra} DUPLICATE' if extra else 'clean'}")
        for k, ids in sorted(dupes.items(), key=lambda kv: str(kv[0])):
            print(f"      {k} -> ids {ids}  (would keep {ids[0]})")

    print("\n  Ledger, counted and never deleted -- see the module docstring:")
    print(f"      movements    {Movement.objects.filter(program_id__in=SCOPES).count()}")
    print(f"      stock counts {StockCount.objects.filter(program_id__in=SCOPES).count()}")
    print(f"\n  {total} duplicate row(s).")
    return total, found


def remove(found):
    """Delete the extras, keeping the lowest id in each group.

    Deleted youngest-first within a group so a protected reference from one
    duplicate to another cannot block the delete.
    """
    for label, (rows, dupes) in found.items():
        for key, ids in dupes.items():
            for pk in reversed(ids[1:]):
                rows().filter(pk=pk).delete()
                print(f"  deleted {label} id {pk} (kept {ids[0]})")


total, found = report()
if total and REMOVE:
    print("\nREMOVE is on. Deleting:")
    remove(found)
    print("\nRe-checking:")
    report()
elif total:
    print("\n  Nothing deleted. Read the above, then set REMOVE = True in this file.")
