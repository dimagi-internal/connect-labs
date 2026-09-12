"""The stock tier through its operations, over real tables.

Exercised through `call_operation` rather than the repository directly,
because that is the surface a screen, the HTTP API, an MCP client and an
agent all share -- and the schemas only bind if they are the thing under
test.
"""

from datetime import date, timedelta
from decimal import Decimal

import jsonschema
import pytest

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Movement
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
OPP = 10501
TODAY = date(2026, 9, 12)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def rutf(da):
    return op(
        da,
        "commodity_upsert",
        data={
            "slug": "rutf",
            "name": "RUTF",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        },
    )


@pytest.fixture
def item_144(da, rutf):
    return op(
        da,
        "item_upsert",
        data={
            "sku": "harmattan-rutf",
            "name": "Harmattan RUTF",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 144,
        },
    )


@pytest.fixture
def item_unspecified(da, rutf):
    """A trade item whose supplier never stated sachets per carton."""
    return op(
        da,
        "item_upsert",
        data={
            "sku": "dabs-rutf",
            "name": "DABS RUTF",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
        },
    )


@pytest.fixture
def store(da):
    return op(
        da,
        "supply_point_upsert",
        data={
            "slug": "central-store",
            "name": "Central store, Kano",
            "kind": "central_store",
            "source": "we_recorded",
            "min_months_of_stock": "2",
            "max_months_of_stock": "6",
        },
    )


@pytest.fixture
def worker(da):
    return op(
        da,
        "supply_point_upsert",
        data={
            "slug": "flw-amina",
            "name": "Amina (field worker)",
            "kind": "user_held",
            "opportunity_id": OPP,
            "connect_username": "flw-amina",
            "source": "we_recorded",
            "min_months_of_stock": "1",
            "max_months_of_stock": "2",
        },
    )


def _stock_the_store(da, store, item, cartons=300):
    op(
        da,
        "movement_record",
        data={
            "kind": "receipt",
            "occurred_on": TODAY.isoformat(),
            "commodity_slug": "rutf",
            "item_id": item["id"],
            "to_supply_point_id": store["id"],
            "quantity": str(cartons),
            "quantity_unit": "carton",
            "source": "partner_reported",
        },
    )


class TestSupplyPoints:
    def test_a_user_held_point_must_name_its_connect_user(self, da):
        with pytest.raises(Exception, match="Connect user"):
            op(
                da,
                "supply_point_upsert",
                data={"slug": "nobody", "name": "Nobody", "kind": "user_held", "source": "we_recorded"},
            )

    def test_a_point_must_say_who_recorded_it(self, da):
        with pytest.raises(jsonschema.ValidationError):
            op(da, "supply_point_upsert", data={"slug": "s", "name": "S", "kind": "facility"})

    def test_upsert_is_by_slug_and_does_not_duplicate(self, da, store):
        again = op(
            da,
            "supply_point_upsert",
            data={
                "slug": "central-store",
                "name": "Central store, Kano (renamed)",
                "kind": "central_store",
                "source": "we_recorded",
            },
        )
        assert again["id"] == store["id"]
        assert len(op(da, "supply_point_list")) == 1

    def test_the_network_can_be_filtered_to_one_opportunity(self, da, store, worker):
        assert len(op(da, "supply_point_list")) == 2
        only_opp = op(da, "supply_point_list", opportunity_id=OPP)
        assert [p["slug"] for p in only_opp] == ["flw-amina"]


class TestDistribution:
    def test_a_run_posts_one_movement_per_line_and_moves_the_balance(self, da, rutf, item_144, store, worker):
        _stock_the_store(da, store, item_144)

        run = op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "partner_reported",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item_144["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )

        assert len(run["lines"]) == 1
        assert run["lines"][0]["movement_id"] is not None
        assert run["witnessed"] is False, "a partner's report is a claim, not an observation"

        store_soh = op(da, "stock_on_hand", supply_point_id=store["id"], item_id=item_144["id"])
        worker_soh = op(da, "stock_on_hand", supply_point_id=worker["id"], item_id=item_144["id"])
        assert store_soh["ledger"]["amount"] == "288"
        assert worker_soh["ledger"]["amount"] == "12"

    def test_a_worker_can_be_named_by_connect_username(self, da, rutf, item_144, store, worker):
        _stock_the_store(da, store, item_144)
        run = op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "partner_reported",
                "lines": [
                    {
                        "connect_username": "flw-amina",
                        "item_id": item_144["id"],
                        "quantity": "5",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        assert run["lines"][0]["to_supply_point_id"] == worker["id"]

    def test_an_unknown_worker_is_refused_with_the_fix_named(self, da, rutf, item_144, store):
        with pytest.raises(ValueError, match="create one of kind user_held first"):
            op(
                da,
                "distribution_record",
                data={
                    "supply_point_id": store["id"],
                    "opportunity_id": OPP,
                    "commodity_slug": "rutf",
                    "distributed_on": TODAY.isoformat(),
                    "source": "partner_reported",
                    "lines": [{"connect_username": "flw-nobody", "quantity": "1", "quantity_unit": "carton"}],
                },
            )

    def test_a_run_with_no_lines_is_refused_by_the_schema(self, da, rutf, store):
        with pytest.raises(jsonschema.ValidationError):
            op(
                da,
                "distribution_record",
                data={
                    "supply_point_id": store["id"],
                    "opportunity_id": OPP,
                    "commodity_slug": "rutf",
                    "distributed_on": TODAY.isoformat(),
                    "source": "partner_reported",
                    "lines": [],
                },
            )

    def test_a_failed_line_rolls_back_the_whole_run(self, da, rutf, item_144, store, worker):
        """A half-posted run would leave the ledger short by whatever the
        failing line carried, and the ledger cannot be edited afterwards."""
        _stock_the_store(da, store, item_144)
        with pytest.raises(ValueError):
            op(
                da,
                "distribution_record",
                data={
                    "supply_point_id": store["id"],
                    "opportunity_id": OPP,
                    "commodity_slug": "rutf",
                    "distributed_on": TODAY.isoformat(),
                    "source": "partner_reported",
                    "lines": [
                        {"to_supply_point_id": worker["id"], "quantity": "5", "quantity_unit": "carton"},
                        {"connect_username": "flw-nobody", "quantity": "5", "quantity_unit": "carton"},
                    ],
                },
            )
        assert op(da, "distribution_list") == []
        assert Movement.objects.filter(kind="distribution").count() == 0


class TestCounts:
    def _count(self, da, point, item, **overrides):
        data = {
            "supply_point_id": point["id"],
            "commodity_slug": "rutf",
            "item_id": item["id"],
            "kind": "self_reported",
            "counted_on": TODAY.isoformat(),
            "quantity": "10",
            "quantity_unit": "carton",
            "source": "commcare_form",
        }
        data.update(overrides)
        return op(da, "stock_count_record", data=data)

    def test_a_self_report_is_kept_without_touching_the_ledger(self, da, rutf, item_144, store, worker):
        _stock_the_store(da, store, item_144, cartons=20)
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item_144["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        self._count(da, worker, item_144, quantity="10")

        soh = op(da, "stock_on_hand", supply_point_id=worker["id"], item_id=item_144["id"])
        assert soh["ledger"]["amount"] == "12"
        assert soh["reported"]["amount"] == "10"
        assert soh["variance"]["amount"] == "-2"
        assert soh["basis"] == "disagreement"
        assert Movement.objects.filter(kind="adjustment").count() == 0

    def test_a_zero_count_is_a_real_observation(self, da, rutf, item_144, worker):
        count = self._count(da, worker, item_144, quantity="0")
        assert count["quantity"] == "0"

    def test_an_override_moves_the_ledger_to_the_asserted_figure(self, da, rutf, item_144, store, worker):
        _stock_the_store(da, store, item_144, cartons=20)
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item_144["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        count = self._count(
            da,
            worker,
            item_144,
            kind="override",
            quantity="9",
            reason="counted on a supervision visit; three cartons unaccounted for",
            source="we_recorded",
        )

        assert count["adjustment_movement_id"] is not None
        adjustment = Movement.objects.get(pk=count["adjustment_movement_id"])
        assert adjustment.quantity == Decimal("-3.0000")
        assert adjustment.kind == "adjustment"

        soh = op(da, "stock_on_hand", supply_point_id=worker["id"], item_id=item_144["id"])
        assert soh["ledger"]["amount"] == "9", "the ledger did not follow the override"
        assert soh["variance"]["amount"] == "0"

    def test_an_override_needs_a_reason(self, da, rutf, item_144, worker):
        with pytest.raises(Exception, match="say why"):
            self._count(da, worker, item_144, kind="override", quantity="5", source="we_recorded")

    def test_an_override_that_cannot_be_reconciled_is_refused_not_guessed(
        self, da, rutf, item_unspecified, store, worker
    ):
        """The store holds cartons, the override is in sachets, and nobody
        stated how many sachets are in a carton. Posting an adjustment would
        write a guess into an append-only ledger."""
        _stock_the_store(da, store, item_unspecified, cartons=20)
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item_unspecified["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        with pytest.raises(ValueError, match="cannot override stock on hand here"):
            self._count(
                da,
                worker,
                item_unspecified,
                kind="override",
                quantity="900",
                quantity_unit="sachet",
                reason="counted the sachets",
                source="we_recorded",
            )
        assert Movement.objects.filter(kind="adjustment").count() == 0


class TestLedgerGuards:
    def test_a_movement_touching_no_point_is_refused_with_the_reason(self, da, rutf, item_144):
        with pytest.raises(ValueError, match="changes no balance"):
            op(
                da,
                "movement_record",
                data={
                    "kind": "receipt",
                    "occurred_on": TODAY.isoformat(),
                    "commodity_slug": "rutf",
                    "quantity": "5",
                    "quantity_unit": "carton",
                    "source": "we_recorded",
                },
            )

    def test_in_transit_is_reported_apart_from_on_hand(self, da, rutf, item_144, store):
        _stock_the_store(da, store, item_144, cartons=255)
        position = op(da, "stock_position", supply_point_id=store["id"], item_id=item_144["id"])
        assert position["on_hand"]["amount"] == "255"
        assert position["in_transit"]["amount"] == "0"
        assert position["available"]["amount"] == "255"


class TestNetworkView:
    def _two_workers(self, da):
        for slug, username in (("flw-a", "flw-a"), ("flw-b", "flw-b")):
            op(
                da,
                "supply_point_upsert",
                data={
                    "slug": slug,
                    "name": slug,
                    "kind": "user_held",
                    "opportunity_id": OPP,
                    "connect_username": username,
                    "source": "we_recorded",
                    "min_months_of_stock": "1",
                    "max_months_of_stock": "2",
                },
            )
        return op(da, "supply_point_list", opportunity_id=OPP, kind="user_held")

    def test_every_worker_appears_even_with_nothing_recorded(self, da, rutf, item_144):
        self._two_workers(da)
        result = op(da, "network_stock", opportunity_id=OPP, item_id=item_144["id"])
        assert result["summary"]["points"] == 2
        assert result["summary"]["never_reported"] == 2
        assert {p["status"] for p in result["points"]} == {"unknown"}

    def test_a_worker_who_cannot_be_computed_carries_its_reason_not_a_zero(self, da, rutf, item_unspecified, store):
        workers = self._two_workers(da)
        _stock_the_store(da, store, item_unspecified, cartons=50)
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": (TODAY - timedelta(days=40)).isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": workers[0]["id"],
                        "item_id": item_unspecified["id"],
                        "quantity": "10",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        # Dispensing recorded in sachets against a carton balance, with no
        # pack spec to bridge them.
        for offset in range(35):
            op(
                da,
                "movement_record",
                data={
                    "kind": "consumption",
                    "occurred_on": (TODAY - timedelta(days=offset)).isoformat(),
                    "commodity_slug": "rutf",
                    "item_id": item_unspecified["id"],
                    "from_supply_point_id": workers[0]["id"],
                    "quantity": "20",
                    "quantity_unit": "sachet",
                    "source": "connect_visit",
                },
            )

        result = op(da, "network_stock", opportunity_id=OPP, item_id=item_unspecified["id"])
        row = next(p for p in result["points"] if p["supply_point_id"] == workers[0]["id"])
        assert "unconfirmed" in row["on_hand"]
        assert any("carton" in reason for reason in row["on_hand"]["unconfirmed"])
        assert result["summary"]["unconfirmed_on_hand"] == 1


class TestStockReportIngest:
    def _rows(self, *usernames, quantity="8", submission_prefix="sub"):
        return [
            {
                "connect_username": username,
                "quantity": quantity,
                "counted_on": TODAY.isoformat(),
                "form_submission_id": f"{submission_prefix}-{username}",
            }
            for username in usernames
        ]

    def test_a_worker_report_lands_as_a_self_reported_count(self, da, rutf, item_144, worker):
        result = op(
            da,
            "stock_report_ingest",
            rows=self._rows("flw-amina"),
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
        )
        assert result["created"] == 1
        counts = op(da, "stock_count_list", supply_point_id=worker["id"])
        assert counts[0]["kind"] == "self_reported"
        assert counts[0]["source"] == "commcare_form"
        assert counts[0]["quantity"] == "8"

    def test_re_reading_the_same_export_does_not_double_post(self, da, rutf, item_144, worker):
        payload = dict(
            rows=self._rows("flw-amina"),
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
        )
        first = op(da, "stock_report_ingest", **payload)
        second = op(da, "stock_report_ingest", **payload)
        assert first["created"] == 1
        assert second["created"] == 0
        assert second["skipped_already_ingested"] == 1
        assert len(op(da, "stock_count_list", supply_point_id=worker["id"])) == 1

    def test_a_duplicate_inside_one_batch_is_also_caught(self, da, rutf, item_144, worker):
        rows = self._rows("flw-amina") + self._rows("flw-amina")
        result = op(
            da,
            "stock_report_ingest",
            rows=rows,
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
        )
        assert result["created"] == 1
        assert result["skipped_already_ingested"] == 1

    def test_an_unknown_worker_is_named_not_invented(self, da, rutf, item_144, worker):
        result = op(
            da,
            "stock_report_ingest",
            rows=self._rows("flw-amina", "flw-typo"),
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
        )
        assert result["created"] == 1
        assert [u["connect_username"] for u in result["unmatched"]] == ["flw-typo"]
        assert op(da, "supply_point_list", opportunity_id=OPP, kind="user_held") != []
        assert len(op(da, "supply_point_list", kind="user_held")) == 1, "a phantom worker was created"

    def test_missing_points_are_created_only_when_asked(self, da, rutf, item_144, worker):
        result = op(
            da,
            "stock_report_ingest",
            rows=self._rows("flw-new"),
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
            create_missing_points=True,
        )
        assert result["created"] == 1
        assert len(result["created_supply_points"]) == 1
        created = op(da, "supply_point_list", opportunity_id=OPP, kind="user_held")
        assert "flw-new" in [p["connect_username"] for p in created]

    def test_a_report_does_not_move_the_ledger(self, da, rutf, item_144, store, worker):
        _stock_the_store(da, store, item_144, cartons=20)
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item_144["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        op(
            da,
            "stock_report_ingest",
            rows=self._rows("flw-amina", quantity="8"),
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
        )
        soh = op(da, "stock_on_hand", supply_point_id=worker["id"], item_id=item_144["id"])
        assert soh["ledger"]["amount"] == "12", "an ingested report moved the ledger"
        assert soh["reported"]["amount"] == "8"
        assert soh["variance"]["amount"] == "-4"
        assert Movement.objects.filter(kind="adjustment").count() == 0

    def test_a_zero_report_is_a_stockout_not_a_missing_answer(self, da, rutf, item_144, worker):
        result = op(
            da,
            "stock_report_ingest",
            rows=self._rows("flw-amina", quantity="0"),
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=OPP,
            item_id=item_144["id"],
        )
        assert result["created"] == 1
        assert op(da, "stock_count_list", supply_point_id=worker["id"])[0]["quantity"] == "0"


class TestExtractRows:
    def test_a_visit_that_did_not_answer_is_skipped_not_read_as_zero(self):
        from connect_labs.supply_chain.stock.services.ingest import extract_rows

        visits = [
            {"id": 1, "username": "a", "visit_date": "2026-09-01", "form": {"stock": {"cartons_on_hand": "6"}}},
            {"id": 2, "username": "b", "visit_date": "2026-09-01", "form": {"stock": {}}},
            {"id": 3, "username": "c", "visit_date": "2026-09-01"},
        ]
        rows = extract_rows(visits, quantity_path="form.stock.cartons_on_hand")
        assert [r["connect_username"] for r in rows] == ["a"]
        assert rows[0]["quantity"] == "6"
        assert rows[0]["form_submission_id"] == "1"

    def test_a_reported_zero_is_kept(self):
        from connect_labs.supply_chain.stock.services.ingest import extract_rows

        rows = extract_rows(
            [{"id": 4, "username": "d", "visit_date": "2026-09-01", "form": {"stock": {"cartons_on_hand": 0}}}],
            quantity_path="form.stock.cartons_on_hand",
        )
        assert rows[0]["quantity"] == 0
