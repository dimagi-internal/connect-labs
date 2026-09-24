"""Unit test for the OES demo reference seeder (`seed_reference`).

`scripts/walkthroughs/oes-demo/` is a script directory, not a Python package
(the hyphen in `oes-demo` can't be a package name), so the module under test
is loaded by file path rather than `import`. Nothing here touches the
database or Drive: `op` is a fake that just records the payloads it was
called with, and `access` is never dereferenced by `seed_reference` itself.

No partner name, quantity or price appears here -- everything below is an
invented placeholder, per the repo's public-repo rule.
"""

import importlib.util
from pathlib import Path

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
