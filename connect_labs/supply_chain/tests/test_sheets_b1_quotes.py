"""The tender page's quote sheets after Sophie types into them (supply-sophie-sheets, batch 1).

THIS REPOSITORY IS PUBLIC. Everything the replay writes is invented.

Three rules, read off the page the walkthrough films:

- a value's source mark comes from the version that last changed THAT value:
  typing Kanem's pack makes the pack a person's, and the price the AI read off
  its email keeps the AI's mark, on the Suppliers sheet and in the comparison;
- the Quotes sheet is one row per quote that stands, its count the number of
  those quotes; the versions a correction replaced sit under the row, each with
  what changed and the reason;
- the Quotes sheet does not repeat the Suppliers sheet's delivery term and
  pack; it keeps each quote's price, since a supplier quoting two products has
  one row there and two here.
"""

import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from django.urls import reverse

HERE = Path(__file__).resolve().parents[3] / "scripts/walkthroughs/supply-sophie-unanswered-round"


def _load(name, filename):
    sys.path.insert(0, str(HERE.parent))
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def replay():
    return _load("unanswered_round_replay_b1_quotes", "replay.py")


@pytest.fixture
def world(replay, client, monkeypatch):
    """Seeded as the sheets narrative is, and Sophie signed in to its program."""
    from connect_labs.supply_chain import cells, form_views, views  # noqa: F401 -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    replay.ensure_program()
    out = replay.seed_world(create_buyer=True, today=dt.date.today(), sheets=True)
    client.force_login(replay.personas()["sophie"])

    def scoped(request):
        access = real_access(request)
        access.program_id = out["program_id"]
        return access

    for name, module in list(sys.modules.items()):
        if not name.startswith("connect_labs.supply_chain") or name.endswith("api_views"):
            continue
        if hasattr(module, "_access"):
            monkeypatch.setattr(module, "_access", scoped)
        if hasattr(module, "has_program_context"):
            monkeypatch.setattr(module, "has_program_context", lambda request: True)
    return {**out, "client": client}


def _quote(**filters):
    from connect_labs.supply_chain.models import Quote

    return Quote.objects.get(superseded_by__isnull=True, voided=False, **filters)


def _kanem():
    return _quote(supplier_reference="KF/Q/2719")


def _type(world, cell, value, was=""):
    """What cell_edit.js posts when Sophie types into a sheet cell."""
    url = reverse("supply_chain:cell_edit") + f"?program_id={world['program_id']}"
    response = world["client"].post(
        url, data=json.dumps({"cell": cell, "value": value, "was": was}), content_type="application/json"
    )
    assert response.status_code == 200, response.content
    return response.json()["key"]


def _page(world, name, *args, query=""):
    url = reverse(f"supply_chain:{name}", args=args) + f"?program_id={world['program_id']}{query}"
    response = world["client"].get(url)
    assert response.status_code == 200
    return response.content.decode()


def _row(body, testid, attr, value):
    """The <tr> carrying data-testid=testid and attr=value."""
    for match in re.finditer(r"<tr\b[^>]*>.*?</tr>", body, re.S):
        head = match.group(0)[: match.group(0).index(">") + 1]
        if f'data-testid="{testid}"' in head and f'{attr}="{value}"' in head:
            return match.group(0)
    raise AssertionError(f"no {testid} row with {attr}={value}")


def _cell(row, testid):
    match = re.search(rf'<td\b[^>]*data-testid="{testid}"[^>]*>(.*?)</td>', row, re.S)
    assert match, testid
    return match.group(1)


def _src(html):
    return re.findall(r'data-src="(\w+)"', html)


@pytest.mark.django_db
def test_typing_kanems_pack_marks_the_pack_and_leaves_its_ai_read_price(world):
    kanem = _kanem()
    tender = kanem.tender_id
    before = _row(
        _page(world, "procurement_tender_detail", tender), "supplier-row", "data-supplier-id", kanem.supplier_id
    )
    assert _src(_cell(before, "supplier-quote")) == ["ai"]

    _type(world, f"quote:{kanem.pk}:base_per_pack_stated", "150")

    kanem = _kanem()
    assert kanem.version == 2 and kanem.base_per_pack_stated == 150
    row = _row(
        _page(world, "procurement_tender_detail", tender), "supplier-row", "data-supplier-id", kanem.supplier_id
    )
    assert _src(_cell(row, "supplier-quote")) == ["ai"]
    assert _src(_cell(row, "supplier-pack")) == ["person"]

    # The comparison reads the same rule, cell by cell.
    grid = _page(world, "procurement_comparison", tender, query=f"&commodity={kanem.commodity.slug}")
    price = re.search(rf'<td\b[^>]*data-fact="price" data-quote-id="{kanem.pk}"[^>]*>(.*?)</td>', grid, re.S)
    pack = re.search(rf'<td\b[^>]*data-fact="pack" data-quote-id="{kanem.pk}"[^>]*>(.*?)</td>', grid, re.S)
    assert _src(price.group(1)) == ["ai"]
    assert _src(pack.group(1)) == ["person"]


@pytest.mark.django_db
def test_the_quotes_sheet_is_one_row_per_standing_quote_with_its_earlier_versions_under_it(world):
    from connect_labs.supply_chain.models import Quote

    kanem = _kanem()
    tender = kanem.tender_id
    standing = Quote.objects.filter(tender_id=tender, superseded_by__isnull=True, voided=False).count()

    first = _type(world, f"quote:{kanem.pk}:received_on", "2026-09-20")
    _type(world, first, "2026-09-21")
    kanem = _kanem()
    assert kanem.version == 3

    body = _page(world, "procurement_tender_detail", tender)
    summary = re.search(r'<summary id="quotes">Quotes <span[^>]*>(\d+)</span>', body)
    assert int(summary.group(1)) == standing
    assert body.count('data-testid="quote-row"') == standing
    assert f'data-testid="quote-row" data-quote-id="{kanem.pk}"' in body
    # The walkthrough types into the standing row's Received cell.
    row = _row(body, "quote-row", "data-quote-id", kanem.pk)
    assert f'data-edit="quote:{kanem.pk}:received_on"' in row
    assert ">v3</span>" in row
    # Both versions it replaced, newest first, each saying what changed -- and no cell to edit.
    versions = re.findall(r'<tr\b[^>]*data-testid="quote-version-row"[^>]*>.*?</tr>', body, re.S)
    assert len(versions) == 2
    assert ">v2</a>" in versions[0] and ">v1</a>" in versions[1]
    assert "21 Sep" in versions[0] and "(was 20 Sep)" in versions[0]
    assert "data-edit" not in "".join(versions)


@pytest.mark.django_db
def test_the_quotes_sheet_leaves_term_and_pack_to_the_suppliers_sheet(world):
    kanem = _kanem()
    _type(world, f"quote:{kanem.pk}:base_per_pack_stated", "150")
    kanem = _kanem()
    body = _page(world, "procurement_tender_detail", kanem.tender_id)
    table = re.search(r'<table[^>]*data-testid="quotes-table".*?</table>', body, re.S).group(0)
    heads = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table)
    assert "Delivery term" not in heads and "Pack size" not in heads and "Per" not in heads
    assert "Price" in heads and "Received" in heads and "Their ref." in heads
    assert ":incoterm" not in table and ":base_per_pack_stated" not in table
    # Its price, with what it is per, keeps the AI's mark through the pack's correction.
    price = _cell(_row(body, "quote-row", "data-quote-id", kanem.pk), "quote-price")
    assert _src(price) == ["ai"] and "per carton" in price
    # It fits its card: no fixed minimum width to push the Detail links out of view.
    assert "min-w-[60rem]" not in table


@pytest.mark.django_db
def test_a_pack_without_a_stated_weight_reads_in_the_same_shape_everywhere(world):
    kanem = _kanem()
    _type(world, f"quote:{kanem.pk}:base_per_pack_stated", "150")
    kanem = _kanem()
    assert kanem.base_unit_grams_stated is None
    row = _row(
        _page(world, "procurement_tender_detail", kanem.tender_id),
        "supplier-row",
        "data-supplier-id",
        kanem.supplier_id,
    )
    pack = re.sub(r"<[^>]+>", "", _cell(row, "supplier-pack")).strip()
    assert pack != "150" and pack.startswith("150 ")
