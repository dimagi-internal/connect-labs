"""Migrations 0022-0023: every supplier becomes a company linked into its program.

THIS REPOSITORY IS PUBLIC. Every company and figure here is invented.

Run against the real migration graph: rolled back to 0021, rows written with
the historical models, then migrated forward. The two cases that lose data if
the fold is wrong are the ones pinned -- one company across two programs, and
two rows for one company inside one program.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("supply_chain", "0021_portfolio")]
AFTER = [("supply_chain", "0023_supplier_is_a_company")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def old_apps(transactional_db):
    apps = _migrate(BEFORE)
    yield apps
    _migrate(executor_leaf_nodes())


def executor_leaf_nodes():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


def _world(apps):
    LabsOrg = apps.get_model("labs", "LabsOrg")
    Supplier = apps.get_model("supply_chain", "Supplier")
    Commodity = apps.get_model("supply_chain", "Commodity")
    Round = apps.get_model("supply_chain", "Round")
    Quote = apps.get_model("supply_chain", "Quote")

    bound = LabsOrg.objects.create(slug="harmattan", name="Harmattan Health Supplies", connect_organization_id=7101)
    chc = Supplier.objects.create(
        scope_key="prog:10501", name="Harmattan Health Supplies", org=bound, type="distributor", city="Kano"
    )
    # The same company in another program, typed without its org and with a
    # contact the first row did not have.
    rutf = Supplier.objects.create(
        scope_key="prog:10502",
        name="Harmattan Health Supplies",
        contacts=[{"name": "A. Bello", "email": "sales@harmattan.example"}],
    )
    # Two rows for one company inside one program: an entry made twice.
    first = Supplier.objects.create(scope_key="prog:10502", name="Plateau Foods", country="NG", notes="first")
    dupe = Supplier.objects.create(scope_key="prog:10502", name="plateau foods", notes="second")

    commodity = Commodity.objects.create(scope_key="prog:10502", slug="rutf", name="RUTF")
    round_ = Round.objects.create(program_id=10502, label="R2")
    quote = Quote.objects.create(round=round_, supplier=dupe, commodity=commodity)
    return {"chc": chc.pk, "rutf": rutf.pk, "first": first.pk, "dupe": dupe.pk, "quote": quote.pk, "bound": bound.pk}


@pytest.mark.django_db(transaction=True)
def test_one_company_across_programs_and_duplicates_folded(old_apps):
    ids = _world(old_apps)

    apps = _migrate(AFTER)
    LabsOrg = apps.get_model("labs", "LabsOrg")
    Supplier = apps.get_model("supply_chain", "Supplier")
    SupplierProfile = apps.get_model("supply_chain", "SupplierProfile")
    Quote = apps.get_model("supply_chain", "Quote")

    # One company across two programs, each program keeping its own link.
    assert Supplier.objects.get(pk=ids["chc"]).org_id == ids["bound"]
    assert Supplier.objects.get(pk=ids["rutf"]).org_id == ids["bound"]
    profile = SupplierProfile.objects.get(org_id=ids["bound"])
    assert profile.type == "distributor"
    assert profile.city == "Kano"
    assert profile.contacts == [{"name": "A. Bello", "email": "sales@harmattan.example"}]

    # A company nobody had an org for is minted, with no Connect id.
    plateau = LabsOrg.objects.get(name="Plateau Foods")
    assert plateau.connect_organization_id is None
    assert plateau.country == "NG"

    # The duplicate is folded into the first, and its quote moves with it.
    assert not Supplier.objects.filter(pk=ids["dupe"]).exists()
    assert Quote.objects.get(pk=ids["quote"]).supplier_id == ids["first"]
    assert "second" in Supplier.objects.get(pk=ids["first"]).notes
