"""The OES demo reference seeder: what it sends, and into which scope.

`scripts/walkthroughs/oes-demo/` is a script directory, not a Python package
(the hyphen in `oes-demo` can't be a package name), so the module under test
is loaded by file path rather than `import`.

Two halves, deliberately different in kind. The first asks what payload one
organisation produces and uses a fake `op` that records calls and touches
nothing. The second asks which PROGRAM each product lands in, and cannot use
a fake: `scope_key` is applied by the data-access layer, so a fake `op` would
agree with a seeder that put all four chains in one catalogue -- the exact
mistake those tests exist to catch. They run the real operations against a
real database.

No partner name, quantity or price appears here -- everything below is an
invented placeholder, per the repo's public-repo rule.
"""

import importlib.util
from pathlib import Path

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import Commodity
from connect_labs.supply_chain.operations import call_operation

_SEED_REMOTE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "walkthroughs" / "oes-demo" / "seed_remote.py"


def _load_seed_remote():
    spec = importlib.util.spec_from_file_location("oes_demo_seed_remote", _SEED_REMOTE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOp:
    """Stands in for `call_operation`: records calls, touches nothing real."""

    def __init__(self):
        self.calls = []

    def __call__(self, access, name, **payload):
        self.calls.append((name, payload))
        return {"op": name, **payload}


# One bound org (carries a real connect_organization_id) and one unbound org
# (carries a connect_organization_slug instead) -- the same shape as the real
# document's five orgs, with invented placeholder names/slugs.
_DOCUMENT = {
    "orgs": [
        {
            "slug": "the-programme-org",
            "name": "A Placeholder Programme Org",
            "country": "NG",
            "connect_organization_id": 359,
            "notes": "bound to its Connect org",
        },
        {
            "slug": "a-partner-org",
            "name": "A Placeholder Partner Org",
            "country": "NG",
            "connect_organization_id": None,
            "connect_organization_slug": "a-partner-connect-slug",
            "notes": "not bound -- Connect's export never returns this org",
        },
    ],
    "commodities": [
        {
            "slug": "a-product",
            "name": "A Placeholder Product",
            "category": "consumable",
            "base_unit": "unit",
        },
    ],
}


def _run_seed_reference():
    module = _load_seed_remote()
    fake_op = _FakeOp()
    module.op = fake_op  # `seed_reference` calls the module-level `op` name at call time
    result = module.seed_reference(access=object(), data=_DOCUMENT)
    return result, fake_op


def _org_payload(fake_op, slug):
    return next(
        payload["data"] for name, payload in fake_op.calls if name == "org_upsert" and payload["data"]["slug"] == slug
    )


def test_every_org_in_the_document_is_upserted():
    result, fake_op = _run_seed_reference()

    assert set(result["orgs"]) == {"the-programme-org", "a-partner-org"}
    org_upserts = [payload for name, payload in fake_op.calls if name == "org_upsert"]
    assert len(org_upserts) == 2


def test_the_bound_orgs_connect_organization_id_reaches_the_payload_as_359():
    _, fake_op = _run_seed_reference()

    data = _org_payload(fake_op, "the-programme-org")
    assert data["connect_organization_id"] == 359


def test_an_unbound_orgs_connect_organization_id_is_omitted_not_sent_as_none():
    """The assertion that matters.

    `connect_organization_id` is the organisation's identity and does not
    change once set (`connect_labs/labs/models.py`'s `LabsOrg` docstring), so
    `org_upsert` correctly refuses an explicit `None` for it. An org with no
    known Connect id must therefore OMIT the key entirely -- never send
    `None` -- while still carrying its `connect_organization_slug`, the
    designed route for "organisation known, numeric id not yet known".

    Mutated: made `seed_reference` always include `connect_organization_id`
    (sending `None` for the unbound org instead of omitting the key). This
    test went red (`KeyError` -> the assertion below fails because the key
    IS present), confirming it actually exercises the omission and isn't
    vacuously true. Reverted before committing.
    """
    _, fake_op = _run_seed_reference()

    data = _org_payload(fake_op, "a-partner-org")
    assert "connect_organization_id" not in data
    assert data["connect_organization_slug"] == "a-partner-connect-slug"


def test_commodities_are_also_upserted():
    result, fake_op = _run_seed_reference()

    assert set(result["commodities"]) == {"a-product"}
    commodity_upserts = [payload for name, payload in fake_op.calls if name == "commodity_upsert"]
    assert len(commodity_upserts) == 1
    assert commodity_upserts[0]["data"]["slug"] == "a-product"


# ======================================================================
# Four scopes, four catalogues
# ======================================================================
#
# `SupplyDataAccess.scope_key` is "the program, always", and it governs the
# catalogue as well as the ledger. CHC, RUTF and chlorine are three different
# real things and the chlorine chain has no Connect opportunity behind it at
# all, so they are three programs -- and the products of one must not appear
# in another's picker. These tests go through the real operations against a
# real database, because that is the only place `scope_key` is actually
# applied: a fake `op` would agree with a seeder that shared one catalogue.


def _commodity(slug, name, category, **extra):
    return {"slug": slug, "name": name, "category": category, **extra}


# The same SHAPE as the Drive document -- four chain sections over one shared
# product list, one of them a kit with components -- with every name invented.
_SCOPED_DOCUMENT = {
    "orgs": _DOCUMENT["orgs"],
    "commodities": [
        # Listed before the kit that contains it, the way the document lists
        # them: `_kit_components` refuses a component that is not already a
        # product in the same catalogue.
        _commodity("a-component", "A Placeholder Component", "micronutrient", base_unit="tablet"),
        _commodity(
            "a-kit",
            "A Placeholder Co-pack",
            "oral_rehydration",
            base_unit="course",
            pack_unit="carton",
            base_per_pack=10,
            components=[{"commodity_slug": "a-component", "quantity": 2, "base_unit": "tablet"}],
        ),
        _commodity("a-therapeutic-food", "A Placeholder Therapeutic Food", "therapeutic_food", base_unit="sachet"),
        _commodity("a-water-treatment", "A Placeholder Water Treatment", "consumable", base_unit="L"),
        _commodity("a-product-nobody-buys", "A Placeholder Nobody Ordered", "diagnostic", base_unit="test"),
    ],
    "chc_chain": {
        "round": {"lines": [{"commodity_slug": "a-kit", "quantity": "1", "quantity_unit": "carton"}]},
        "quotes": [{"commodity_slug": "a-kit", "item": {"sku": "SKU-1", "name": "A Placeholder Item"}}],
    },
    "rutf_rounds": {
        "round_one": {"lines": [{"commodity_slug": "a-therapeutic-food", "quantity": "1", "quantity_unit": "carton"}]}
    },
    "chlorine_blocked": {
        "round": {"lines": [{"commodity_slug": "a-water-treatment", "quantity": "1", "quantity_unit": "jerry_can"}]}
    },
    "supply_only": {
        "round": {"lines": [{"commodity_slug": "a-therapeutic-food", "quantity": "1", "quantity_unit": "carton"}]}
    },
}


@pytest.fixture
def scopes(db):
    """Every scope seeded, for real, into its own program.

    `db` rather than a module-level `django_db` mark, so the payload tests
    above keep running without a database and keep saying so.

    `op` is wrapped rather than replaced: the real operation still runs, and
    the wrapper records which program each write went to. That is how the
    "seeded once" claims below can go red -- `upsert_org` is an upsert, so
    seeding the organisations four times leaves exactly the same rows behind
    and a test that only counted rows would pass either way.
    """
    module = _load_seed_remote()
    calls = []
    operation = module.op

    def recording(access, name, **payload):
        calls.append((access.program_id, name, payload.get("data", {}).get("slug")))
        return operation(access, name, **payload)

    module.op = recording
    return module, module.seed_scopes(_SCOPED_DOCUMENT), calls


def _catalogue(module, scope_name):
    """What this program's product picker would actually offer."""
    access = module.access_for(module.SCOPES[scope_name]["program_id"])
    return {row["slug"] for row in call_operation("commodity_list", access, {})}


def test_the_chlorine_product_is_not_in_the_chc_catalogue(scopes):
    """The assertion this whole task exists for.

    One scope holding all three chains would offer chlorine to a CHC buyer
    and ORS to a safe-water one. Mutated `seed_scopes` to seed the whole
    catalogue into every scope (`commodity_slugs=None`) and watched both
    directions go red before trusting them.
    """
    module, _, _ = scopes

    assert "a-water-treatment" not in _catalogue(module, "chc")
    assert "a-kit" not in _catalogue(module, "chlorine")
    assert "a-component" not in _catalogue(module, "chlorine")


def test_each_chain_gets_the_product_it_is_actually_about(scopes):
    module, _, _ = scopes

    assert "a-kit" in _catalogue(module, "chc")
    assert "a-water-treatment" in _catalogue(module, "chlorine")
    assert "a-therapeutic-food" in _catalogue(module, "rutf")
    assert "a-therapeutic-food" in _catalogue(module, "supply_only")


def test_a_kit_brings_its_components_into_the_same_catalogue(scopes):
    """A co-pack without its contents is a co-pack whose spec cannot be checked.

    `_kit_components` refuses a component that is not a product in the same
    catalogue, and "no requirement to fail" reads on screen as a pass. The
    component is named by no round, so only the closure over `components`
    puts it here.
    """
    module, _, _ = scopes

    assert {"a-kit", "a-component"} <= _catalogue(module, "chc")


def test_a_product_no_chain_names_reaches_no_catalogue(scopes):
    """Seeding the whole document into every scope would hide the split.

    A product nothing in the document orders is the cheapest evidence that
    the filter is doing something: it is defined, and it is nowhere.
    """
    module, _, _ = scopes

    assert not Commodity.objects.filter(slug="a-product-nobody-buys").exists()


def test_a_product_two_chains_buy_gets_a_row_in_each_of_their_programs(scopes):
    """The catalogues are separate registries, not one with a filter over it.

    `a-therapeutic-food` is bought by two of the four chains, so it exists
    twice, under two different `scope_key`s. That is the domain's own design
    (`Commodity` is program-scoped) and it is what makes "the RUTF supplier
    register is not the ORS one" true -- each program's products are its own
    rows and editing one leaves the other alone.
    """
    module, seeded, calls = scopes

    keys = {row.scope_key for row in Commodity.objects.filter(slug="a-therapeutic-food")}
    assert len(keys) == 2
    assert len({scope["program_id"] for scope in seeded.values()}) == 4


def test_the_organisations_are_shared_rather_than_seeded_per_scope(scopes):
    """`upsert_org` is the one reference write here that is not program-scoped.

    "An organisation is the same organisation in every program it appears
    in, and scoping it per program is what produced three registries of the
    same thing." Four scopes must therefore leave one row per organisation,
    not four.
    """
    module, seeded, calls = scopes

    upserts = [slug for _, name, slug in calls if name == "org_upsert"]
    assert sorted(upserts) == sorted(row["slug"] for row in _SCOPED_DOCUMENT["orgs"])
    for row in _SCOPED_DOCUMENT["orgs"]:
        assert LabsOrg.objects.filter(slug=row["slug"]).count() == 1
    assert set(seeded["chc"]["reference"]["orgs"]) == {row["slug"] for row in _SCOPED_DOCUMENT["orgs"]}


def test_a_chain_naming_a_product_the_document_never_defined_is_refused():
    """Refused by name rather than seeded against a catalogue missing the point.

    No database: this is the derivation, not the write.
    """
    module = _load_seed_remote()
    section = {"round": {"lines": [{"commodity_slug": "a-product-never-defined"}]}}

    with pytest.raises(ValueError) as caught:
        module.commodities_for(section, _SCOPED_DOCUMENT["commodities"])
    assert "a-product-never-defined" in str(caught.value)


def test_a_missing_document_section_is_refused_by_the_name_of_its_scope():
    module = _load_seed_remote()
    document = {key: value for key, value in _SCOPED_DOCUMENT.items() if key != "chlorine_blocked"}

    with pytest.raises(ValueError) as caught:
        module.seed_scopes(document)
    assert "chlorine_blocked" in str(caught.value) and "chlorine" in str(caught.value)
