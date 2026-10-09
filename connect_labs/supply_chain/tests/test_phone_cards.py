"""Below sm the supply tables become one card per row, the decision first (#2308).

The CSS is in supply_chain/base.html; these pin what it keys on: each table is marked
`phone-cards`, its name cell `phone-title`, the column the reader came for `phone-lead`,
and every other cell carries the `data-label` the card shows beside it.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import re

import pytest

from connect_labs.supply_chain.tests import test_worker_screens as tws
from connect_labs.supply_chain.tests.test_worker_screens import (  # noqa: F401 -- fixtures
    client_in_program,
    da,
    get,
    world,
)

pytestmark = pytest.mark.django_db


def _first_row(body, testid):
    table = re.search(rf'<table[^>]*data-testid="{testid}"[^>]*>(.*?)</table>', body, re.S)
    assert table, f"no {testid}"
    assert "phone-cards" in re.search(rf'<table[^>]*data-testid="{testid}"[^>]*>', body).group(0)
    return re.search(r"<tbody[^>]*>\s*<tr[^>]*>(.*?)</tr>", table.group(1), re.S).group(1)


def _cells(row):
    """[(classes, data-label or None)] for each cell of a row."""
    out = []
    for attrs in re.findall(r"<td([^>]*)>", row):
        cls = re.search(r'class="([^"]*)"', attrs)
        label = re.search(r'data-label="([^"]*)"', attrs)
        out.append((cls.group(1) if cls else "", label.group(1) if label else None))
    return out


def _lead_labels(cells):
    return [label for cls, label in cells if "phone-lead" in cls.split()]


def _every_cell_is_named(cells):
    return all("phone-title" in cls.split() or label for cls, label in cells)


@pytest.mark.parametrize(
    "page, lead",
    [("stock", ["Enough stock?"]), ("workers", ["Days to stock-out"]), ("movements", ["Quantity"])],
)
def test_a_table_leads_its_phone_card_with_the_decision(client_in_program, world, page, lead):  # noqa: F811
    params = {"supply_point_id": world["worker-baobab"].pk} if page == "movements" else {}
    cells = _cells(_first_row(get(client_in_program, page, **params), f"{page}-table"))
    assert _lead_labels(cells) == lead
    assert _every_cell_is_named(cells)
    assert sum("phone-title" in cls.split() for cls, _ in cells) == 1


def test_the_card_rule_lives_in_the_supply_shell(client_in_program, world):  # noqa: F811
    body = get(client_in_program, "stock")
    assert "@media (max-width: 639px)" in body and "table.phone-cards > thead { display: none; }" in body


def test_the_header_picker_has_a_short_name_for_a_phone(client_in_program, world, monkeypatch):  # noqa: F811
    def call(self, request):
        request.labs_context = {
            "program_id": tws.PROGRAM,
            "program": {"id": tws.PROGRAM, "name": "Northern clone"},
            "opportunity": {"id": 7, "name": "Opp seven", "visit_count": 3},
        }
        return self.get_response(request)

    monkeypatch.setattr(tws._ProgramContextMiddleware, "__call__", call)
    body = get(client_in_program, "stock")
    short = re.search(r'data-testid="context-short"[^>]*>([^<]*)<', body).group(1)
    assert short == "Northern clone"  # the programme alone, not "Program: … | Opp: …"
    # Desktop keeps the full line.
    assert re.search(r'class="ctx-full[^"]*"', body) and "Opp seven" in body
