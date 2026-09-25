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
    result = module.seed_reference(access=object(), data=_DOCUMENT, commodity_slugs=["a-product"])
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
        # `pack_unit` matters for round 2's tests below: `compute_figures`
        # converts a quote's `quantity_basis_unit` of "carton" against this.
        _commodity(
            "a-therapeutic-food",
            "A Placeholder Therapeutic Food",
            "therapeutic_food",
            base_unit="sachet",
            pack_unit="carton",
        ),
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


# ---- and the wrong call is refused rather than discouraged --------------
#
# The split is only worth having if the call that undoes it is hard to make.
# A defaulted `commodity_slugs` made "seed every chain's products into this
# one program" the SHORTEST call in the module, and the plan's own Task 5
# code block still contains it. These two pin that it now fails, and fails
# before anything is written.


def test_seeding_a_catalogue_without_saying_which_products_is_refused_at_the_call_site():
    """`commodity_slugs` is keyword-only and has no default.

    Mutated it back to `commodity_slugs=None` on both seeders and watched
    this go red: with a default, the call below succeeds and quietly seeds
    the whole document into one program.
    """
    module = _load_seed_remote()
    module.op = _FakeOp()

    with pytest.raises(TypeError) as caught:
        module.seed_reference(object(), _SCOPED_DOCUMENT)
    assert "commodity_slugs" in str(caught.value)

    with pytest.raises(TypeError) as caught:
        module.seed_catalogue(object(), _SCOPED_DOCUMENT)
    assert "commodity_slugs" in str(caught.value)


def test_asking_for_every_product_by_passing_none_is_refused_before_anything_is_written():
    """`None` is not a back door to the default that was just removed.

    A permissive value is a default in everything but name. The refusal
    happens before `seed_orgs`, so a call that does not say which products
    leaves nothing behind -- checked by asserting the fake `op` recorded no
    write at all, not merely that the catalogue was empty.
    """
    module = _load_seed_remote()
    fake_op = _FakeOp()
    module.op = fake_op

    with pytest.raises(ValueError) as caught:
        module.seed_reference(object(), _SCOPED_DOCUMENT, commodity_slugs=None)

    assert "seed_scopes" in str(caught.value) and "commodities_for" in str(caught.value)
    assert fake_op.calls == []


# ---- the portfolio, and the slugs it resolves through --------------------
#
# The document names its members by SCOPE SLUG, so `SCOPES` stays the one
# place a program id is written down. That agreement is what these pin: a
# second map would drift, and a slug quietly dropped would produce a
# portfolio short by a chain -- which is exactly what the master view exists
# to prevent.

_PORTFOLIO_DOCUMENT = {
    "portfolio": {
        "slug": "a-placeholder-portfolio",
        "name": "A Placeholder Portfolio",
        "program_slugs": ["chc", "chlorine"],
    }
}


@pytest.mark.django_db
def test_the_portfolios_slugs_resolve_to_program_ids_through_the_one_scopes_map():
    """Mutated `SCOPES["chlorine"]["program_id"]` and watched this follow it."""
    module = _load_seed_remote()

    seeded = module.seed_portfolio(_PORTFOLIO_DOCUMENT)

    assert seeded["program_ids"] == [
        module.SCOPES["chc"]["program_id"],
        module.SCOPES["chlorine"]["program_id"],
    ]
    assert seeded["url"] == "/supply/portfolios/a-placeholder-portfolio/"


@pytest.mark.django_db
def test_seeding_the_portfolio_twice_does_not_add_a_second_one():
    """The seed has no purge, so a second run must replace rather than double."""
    module = _load_seed_remote()
    from connect_labs.supply_chain.portfolio.models import Portfolio

    module.seed_portfolio(_PORTFOLIO_DOCUMENT)
    module.seed_portfolio(_PORTFOLIO_DOCUMENT)

    assert Portfolio.objects.filter(slug="a-placeholder-portfolio").count() == 1


@pytest.mark.django_db
def test_a_program_slug_the_scopes_map_does_not_know_is_refused_by_name():
    """Task 11's concern 5, closed.

    Skipping it would seed a portfolio short by one chain, saying nothing
    about the one it dropped -- the misinformation put into the DATA, where
    no view can correct it. Mutated the seeder to skip unknown slugs instead:
    the portfolio was written with one program and this went green only after
    the refusal was restored.
    """
    module = _load_seed_remote()
    from connect_labs.supply_chain.portfolio.models import Portfolio

    document = {"portfolio": {**_PORTFOLIO_DOCUMENT["portfolio"], "program_slugs": ["chc", "not-a-scope"]}}

    with pytest.raises(ValueError) as caught:
        module.seed_portfolio(document)

    assert "not-a-scope" in str(caught.value)
    # Nothing written: a refusal after the write would leave the short
    # portfolio behind and only complain about it.
    assert not Portfolio.objects.filter(slug="a-placeholder-portfolio").exists()


@pytest.mark.django_db
def test_a_portfolio_naming_no_programs_at_all_is_refused():
    module = _load_seed_remote()

    with pytest.raises(ValueError) as caught:
        module.seed_portfolio({"portfolio": {**_PORTFOLIO_DOCUMENT["portfolio"], "program_slugs": []}})
    assert "nothing for it to span" in str(caught.value)


@pytest.mark.django_db
def test_a_document_with_no_portfolio_section_is_refused_by_the_name_of_the_section():
    module = _load_seed_remote()

    with pytest.raises(ValueError) as caught:
        module.seed_portfolio({})
    assert "'portfolio'" in str(caught.value)


# ======================================================================
# RUTF round 2: three suppliers, three reasons, no ranking
# ======================================================================
#
# Task 8's acceptance test. Round 2 (design section 7/8, beats 1-3) has to
# stay genuinely incomparable -- 0 of 3 quotes ranked, each blocked for a
# DIFFERENT reason in the product's own vocabulary. Verified once by hand
# against a rendered `/supply/procurement/rounds/<id>/compare/` response
# before this test was written (see task-8-report.md): "0 of 3 comparable",
# and the three distinct reasons below appeared verbatim on the page.
#
# `supplier_label` (not an org slug) is the point of `supplier_for_label`:
# these three are named, not identified, and must carry no `org_id`.

_ROUND_TWO = {
    "round": {
        "label": "A Placeholder Round 2",
        "delivery_point": {"city": "A Placeholder City"},
        "lines": [{"commodity_slug": "a-therapeutic-food", "quantity": "2000", "quantity_unit": "carton"}],
    },
    "quotes": [
        {
            # Fails at pricing._pack_spec: no trade item, no stated pack spec.
            "supplier_label": "Placeholder Supplier A",
            "commodity_slug": "a-therapeutic-food",
            "as_quoted_unit": "per_pack",
            "as_quoted_amount": "41.00",
            "as_quoted_currency": "USD",
            "pack_spec_source": "not_stated",
            "freight_basis": "included",
            "duties_basis": "included",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
        },
        {
            # Fails at the round-quantity branch of compute_figures (2,400
            # quoted, round asks 2,000) -- and, separately, at pricing._extras
            # (freight basis not specified). Two reasons on purpose.
            "supplier_label": "Placeholder Supplier B",
            "commodity_slug": "a-therapeutic-food",
            "as_quoted_unit": "per_base_unit",
            "as_quoted_amount": "0.29",
            "as_quoted_currency": "USD",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": 150,
            "quantity_basis": "2400",
            "quantity_basis_unit": "carton",
            "freight_basis": "not_specified",
            "duties_basis": "included",
        },
        {
            # Fails at pricing._extras: duties excluded, no amount recorded.
            "supplier_label": "Placeholder Supplier C",
            "commodity_slug": "a-therapeutic-food",
            "as_quoted_unit": "per_pack",
            "as_quoted_amount": "38.50",
            "as_quoted_currency": "USD",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": 150,
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "freight_basis": "included",
            "duties_basis": "excluded",
        },
    ],
}


class _FakeOpForRoundTwo:
    """A fake `op` that can drive `seed_rutf_round_two` with no database.

    Different from `_FakeOp` above: `supplier_for_label` reads the result of
    `supplier_list` as a list of supplier dicts (`for existing in op(...)`),
    so the payload-echoing fake used for `seed_reference` -- which would hand
    back a dict here -- cannot stand in for it. This one answers each
    operation the way the real one would, just without a database.
    """

    def __init__(self):
        self.calls = []
        self._id = 0

    def _next_id(self):
        self._id += 1
        return self._id

    def __call__(self, access, name, **payload):
        self.calls.append((name, payload))
        if name == "supplier_list":
            return []
        if name == "round_list":
            # An empty scope, which is what a first seed sees. `round_for`
            # matches an existing round by label before creating one, so this
            # is the branch that ends in `round_create` below.
            return []
        if name in ("supplier_create", "quote_record"):
            return {"id": self._next_id(), **payload["data"]}
        if name == "round_create":
            return {"id": self._next_id(), **payload["data"]}
        if name == "round_open":
            return {"id": payload["round_id"], "status": "open"}
        raise AssertionError(f"unexpected operation {name!r}")


def test_round_two_never_sends_the_labels_own_descriptive_fields_to_quote_record():
    """`supplier_label`, `supplier_country` and `supplier_note` describe the
    supplier for a human reading the document. None of them is a field
    `quote_record` understands, and they are popped explicitly rather than
    left to `_columns`' silent drop -- so this pins the payload itself, not
    just the eventual database row.

    Every value here is invented, per the module docstring's public-repo rule
    -- this is shaped like the real document (which does carry real firm
    names in these two fields) without repeating one.
    """
    fake_op = _FakeOpForRoundTwo()
    module = _load_seed_remote()
    module.op = fake_op

    round_two = {
        "round": {"label": "A Placeholder Round", "delivery_point": {"city": "A Placeholder City"}, "lines": []},
        "quotes": [
            {
                "supplier_label": "A Placeholder Manufacturer",
                "supplier_country": "NG",
                "supplier_note": "A placeholder note about a placeholder firm.",
                "commodity_slug": "a-therapeutic-food",
                "as_quoted_amount": "1.00",
            }
        ],
    }

    module.seed_rutf_round_two(object(), round_two)

    quote_payloads = [payload["data"] for name, payload in fake_op.calls if name == "quote_record"]
    assert len(quote_payloads) == 1
    for key in ("supplier_label", "supplier_country", "supplier_note"):
        assert key not in quote_payloads[0]


def _seed_and_compare_round_two(module, seeded_scopes, round_two=_ROUND_TWO):
    """Round 2 seeded against the real `rutf` scope, then compared for real.

    Goes through `compare_round` -- the same function
    `procurement/views.py`'s compare page calls -- rather than re-deriving the
    figures here, so this test would fail if the page's own comparison logic
    changed underneath it.
    """
    from connect_labs.supply_chain.procurement.services.comparison import compare_round

    access = seeded_scopes["rutf"]["access"]
    seeded = module.seed_rutf_round_two(access, round_two)

    round_ = access.get_round(seeded["round"]["id"])
    commodity = access.get_commodity("a-therapeutic-food")
    quotes = access.list_quotes(round_id=round_.id)
    suppliers_by_id = {row["id"]: access.get_supplier(row["id"]) for row in seeded["suppliers"]}
    return compare_round(round_, commodity, quotes, suppliers_by_id), seeded


def _round_quantity_reasons(comparison):
    """The `landed_total_for_round_quantity` reason on each blocked row, by supplier name.

    This is the figure the comparison ranks by (`COMPARABILITY_FIELDS`,
    `ranked_by` in `comparison.py`), so it is the reason that actually keeps a
    row out of the ranking -- the one a person reading the page sees as "why
    can't I rank this".
    """
    return {row.supplier_name: row.figures["landed_total_for_round_quantity"].reasons for row in comparison.blocked}


@pytest.mark.django_db
def test_round_two_suppliers_quote_us_and_are_not_organisations_of_ours(scopes):
    """A supplier we only have a name for -- `supplier_for_label`, not `supplier_for_org`.

    This used to assert `org_id is None`, and that premise died with #2019: a
    supplier IS a company now, so `supplier_create` given a bare name mints a
    LabsOrg for it rather than leaving it unattributed. The assertion was
    updated rather than deleted, because what it was protecting is still true
    and still worth protecting -- it just has a different shape.

    The invariant is no longer "no organisation". It is that the organisation
    minted for a firm that merely QUOTED us is not one of ours: it carries no
    `connect_organization_id`, because there is no Connect org behind it and
    inventing one would assert a relationship the seed document does not
    claim. `supplier_for_org` remains the other path, for the distributor,
    which really is an organisation of ours and needs to be the same body at
    both ends of the chain.
    """
    from connect_labs.labs.models import LabsOrg

    module, seeded_scopes, _ = scopes

    _, seeded = _seed_and_compare_round_two(module, seeded_scopes)

    assert {row["name"] for row in seeded["suppliers"]} == {
        "Placeholder Supplier A",
        "Placeholder Supplier B",
        "Placeholder Supplier C",
    }
    orgs = LabsOrg.objects.filter(id__in=[row["org_id"] for row in seeded["suppliers"] if row.get("org_id")])
    assert orgs.count() == 3, "each quoting firm should resolve to its own company"
    assert all(
        org.connect_organization_id is None for org in orgs
    ), "a firm that only quoted us must not be given a Connect organisation id"


@pytest.mark.django_db
def test_round_two_is_not_comparable_and_each_quote_fails_for_a_distinct_reason(scopes):
    """The acceptance test for the whole task.

    0 of 3 comparable, and the three reasons are DISTINCT -- each names the
    one fact this supplier's quote is missing, not a shared "not enough
    information" fog. Mutated below to prove this is a real assertion, not
    one that passes regardless of the data.
    """
    module, seeded_scopes, _ = scopes

    comparison, _ = _seed_and_compare_round_two(module, seeded_scopes)

    assert comparison.comparable_count == 0
    assert len(comparison.blocked) == 3

    reasons = _round_quantity_reasons(comparison)
    assert reasons.keys() == {"Placeholder Supplier A", "Placeholder Supplier B", "Placeholder Supplier C"}

    # Three distinct failure modes, in the product's own words.
    assert any("pack spec not stated" in r for r in reasons["Placeholder Supplier A"])
    assert any("2400" in r and "2000" in r for r in reasons["Placeholder Supplier B"])
    assert any(
        "duties excluded from the quote but no duties amount recorded" in r for r in reasons["Placeholder Supplier C"]
    )

    # And they are genuinely distinct from one another -- not the same
    # reason worded three ways.
    all_reasons = {r for rs in reasons.values() for r in rs}
    assert len(all_reasons) == 3


@pytest.mark.django_db
def test_supplier_a_becomes_comparable_once_it_states_a_pack_spec(scopes):
    """The mutation the task asks for, kept as a permanent regression test.

    Supplier A's whole reason for being incomparable is the missing pack
    spec (design section 7/8): once it states one the same way B and C do,
    `pricing._pack_spec` stops returning Unconfirmed and A's landed totals
    become real numbers. If this ever went green with A still blocked, round
    2's seed data would have quietly stopped making the point the beat is
    built on.

    Confirmed by hand: reverting this quote back to `pack_spec_source:
    "not_stated"` (round 2's actual seed data) makes
    `test_round_two_is_not_comparable_and_each_quote_fails_for_a_distinct_reason`
    go red, because Supplier A would then be comparable and the assertion
    that all three are blocked would fail.
    """
    module, seeded_scopes, _ = scopes
    mutated = {
        **_ROUND_TWO,
        "quotes": [
            {
                **_ROUND_TWO["quotes"][0],
                "pack_spec_source": "stated_on_quote",
                "base_per_pack_stated": 150,
            },
            *_ROUND_TWO["quotes"][1:],
        ],
    }

    comparison, _ = _seed_and_compare_round_two(module, seeded_scopes, round_two=mutated)

    reasons = _round_quantity_reasons(comparison)
    assert "Placeholder Supplier A" not in reasons
    # B and C are still blocked, for their own unrelated reasons.
    assert reasons.keys() == {"Placeholder Supplier B", "Placeholder Supplier C"}


def test_re_seeding_does_not_drag_a_bought_round_back_onto_the_market():
    """`round_for` made the seeder idempotent and broke the line after it.

    `round_open` used to follow `round_create`, so it always acted on a fresh
    draft. Once rounds were matched by label, a second run could hand an
    ALREADY AWARDED round to the same `round_open` call -- and since the
    supplier marketplace shipped, an open round is public. Re-seeding would
    have re-published rounds decided weeks earlier and invited quotes for
    goods already bought.

    MUTATED: `opened` reverted to opening unconditionally. This test went
    red on the awarded round. Reverted.

    Checked at the seeder rather than through a full re-seed because the
    hazard is one line's precondition, and a test that needs a whole
    environment to prove it will not be run.
    """
    module = _load_seed_remote()
    calls = []

    def fake_op(access, name, **payload):
        calls.append(name)
        return {"id": 1, "status": "open"}

    module.op = fake_op

    # A draft is opened...
    assert module.opened(object(), {"id": 1, "status": "draft"})["status"] == "open"
    assert calls == ["round_open"]

    # ...and anything already decided is left exactly as it was.
    calls.clear()
    for settled in ("open", "closed", "awarded"):
        assert module.opened(object(), {"id": 1, "status": settled})["status"] == settled
    assert calls == [], "a round that is not a draft must not be opened again"
