"""Drop the tables of the retired OES demo satellite (Django app label `supply`).

The app (`connect_labs/supply/`, served at `/oes/`) was removed from the repo on
2026-10-14. Removing an app from INSTALLED_APPS leaves its tables, its
`django_migrations` rows, its content types and its permissions behind, so this
clears them. Its data was entirely synthetic (an invented demo world, reseedable
from code that no longer exists), so dropping it is the intent.

Every table is named explicitly. They are all `supply_*`, a prefix the live
`supply_chain` app's `supply_chain_*` tables share, so a pattern would be one typo
away from dropping real data. The list was generated from the app's models before
they were deleted and cross-checked against `pg_tables` on prod.

Irreversible by design: reverse is a no-op, there is nothing to restore.
"""

from django.db import migrations

RETIRED_SUPPLY_TABLES = [
    "supply_action",
    "supply_api_token",
    "supply_appropriation",
    "supply_audit_log",
    "supply_award",
    "supply_bid",
    "supply_bid_score",
    "supply_caseload_estimate",
    "supply_certification",
    "supply_child_outcome",
    "supply_contract",
    "supply_discrepancy",
    "supply_distribution_plan",
    "supply_distribution_record",
    "supply_eoi_review",
    "supply_eoi_round",
    "supply_eoi_submission",
    "supply_event",
    "supply_lot",
    "supply_lot_bid",
    "supply_milestone",
    "supply_node",
    "supply_qualification",
    "supply_rfp",
    "supply_shipment",
    "supply_shipment_line",
    "supply_shortfall_signal",
    "supply_staff_role",
    "supply_supplier_member",
    "supply_supplier_org",
]

_SUPPLY_PERMS = (
    "SELECT p.id FROM auth_permission p JOIN django_content_type ct ON ct.id = p.content_type_id "
    "WHERE ct.app_label = 'supply'"
)

DROP_SQL = [f'DROP TABLE IF EXISTS "{table}" CASCADE;' for table in RETIRED_SUPPLY_TABLES] + [
    f"DELETE FROM auth_group_permissions WHERE permission_id IN ({_SUPPLY_PERMS});",
    f"DELETE FROM users_user_user_permissions WHERE permission_id IN ({_SUPPLY_PERMS});",
    "DELETE FROM django_admin_log WHERE content_type_id IN "
    "(SELECT id FROM django_content_type WHERE app_label = 'supply');",
    f"DELETE FROM auth_permission WHERE id IN ({_SUPPLY_PERMS});",
    "DELETE FROM django_content_type WHERE app_label = 'supply';",
    "DELETE FROM django_migrations WHERE app = 'supply';",
]


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0029_workspace_llos_and_users"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("users", "0001_initial"),
    ]

    operations = [migrations.RunSQL(DROP_SQL, reverse_sql=migrations.RunSQL.noop)]
