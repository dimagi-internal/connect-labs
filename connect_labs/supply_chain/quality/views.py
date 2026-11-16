"""The Tests page: record a test, see every test with its result, filter by partner, download them.

Reads and writes go through the operations (`quality_test_list`, `quality_test_record`,
`quality_test_withdraw`, `checks_list`), like every Supply page. A partner's own view is the same
page with `?partner=<organisation id>`, as on Issues.
"""

import csv
from datetime import date

from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.http import urlencode

from connect_labs.supply_chain import records
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.quality.forms import KIND_TITLES, QualityTestForm, QualityTestWithdrawForm
from connect_labs.supply_chain.values import day_text
from connect_labs.supply_chain.views import OperationBase

FILTERS = ("partner", "kind", "result", "batch", "since", "until", "show")
KIND_WORDS = {"batch": "Batch test", "dispenser_sample": "Dispenser sample", "storage": "Storage check"}
RESULT_WORDS = {"pass": "Pass", "fail": "Fail", "incomplete": "Incomplete"}


def _int(value):
    return int(value) if (value or "").isdigit() else None


def _day(value):
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def _query(request) -> dict:
    """The page's filters as they were asked for, cleaned: what the list, the CSV and the links share."""
    get = request.GET
    kind = get.get("kind") if get.get("kind") in records.QUALITY_TEST_KINDS else None
    result = get.get("result") if get.get("result") in records.QUALITY_TEST_RESULTS else None
    return {
        "partner": _int(get.get("partner")),
        "kind": kind,
        "result": result,
        "batch": (get.get("batch") or "").strip()[:64] or None,
        "since": _day(get.get("since")),
        "until": _day(get.get("until")),
        "show": "withdrawn" if get.get("show") == "withdrawn" else None,
    }


def _tests(op, query, limit=5000):
    return op(
        "quality_test_list",
        partner_org_id=query["partner"],
        kind=query["kind"],
        result=query["result"],
        batch=query["batch"],
        since=query["since"],
        until=query["until"],
        include_withdrawn=query["show"] == "withdrawn",
        limit=limit,
    )


def _partners(program_id) -> list[dict]:
    """The organisations that run a store in this programme, by name: the partner filter."""
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    ids = {org for org in partner_by_point(program_id).values() if org is not None}
    return [{"id": o.pk, "name": o.name} for o in LabsOrg.objects.filter(pk__in=ids).order_by("name")]


def tests_url(**params) -> str:
    kept = {k: v for k, v in params.items() if v not in (None, "")}
    url = reverse("supply_chain:tests")
    return f"{url}?{urlencode(kept)}" if kept else url


def _reading_columns(rows) -> list[str]:
    """Every reading any listed test carries, in a steady order: pH first, the average last."""
    seen = []
    for row in rows:
        for name in row["readings"]:
            if name not in seen:
                seen.append(name)
    return sorted(seen, key=lambda n: (n != "pH", n == records.TRIALS_AVERAGE_FIELD, n))


class TestsView(OperationBase):
    template_name = "supply_chain/tests.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        program_id = _access(self.request).program_id
        query = _query(self.request)
        points = {p["id"]: p for p in self.op("supply_point_list", include_inactive=True)}
        partners = _partners(program_id)
        rows = _tests(self.op, query, limit=1000)
        reading_columns = _reading_columns(rows)
        for row in rows:
            row["point"] = points.get(row["supply_point_id"])
            row["kind_words"] = KIND_WORDS.get(row["kind"], row["kind"])
            row["result_words"] = RESULT_WORDS.get(row["result"], row["result"])
            row["reading_cells"] = [row["readings"].get(name, "") for name in reading_columns]
            row["storage_noes"] = [records.STORAGE_CHECK_WORDS.get(k, k) for k, v in row["storage"].items() if not v]
            row["withdraw_url"] = reverse("supply_chain:test_withdraw", args=[row["id"]])
        failing = [
            c
            for c in self.op("checks_list", kinds=["quality_test_failed"])["checks"]
            if query["partner"] is None
            or c["facts"]["supply_point"]["id"] in _partner_points(program_id, query["partner"])
        ]
        recent_since = date.fromordinal(date.today().toordinal() - 30).isoformat()
        recent = [r for r in rows if r["tested_on"] >= recent_since and not r["withdrawn"]]
        here = {k: v for k, v in query.items() if v}
        context.update(
            query=query,
            here=here,
            rows=rows,
            reading_columns=[_column_words(n) for n in reading_columns],
            failing=failing,
            partners=partners,
            partner_name=next((p["name"] for p in partners if p["id"] == query["partner"]), None),
            kinds=[(k, KIND_WORDS[k]) for k in records.QUALITY_TEST_KINDS],
            results=[(k, RESULT_WORDS[k]) for k in records.QUALITY_TEST_RESULTS],
            new_links=[
                (
                    k,
                    KIND_TITLES[k],
                    reverse("supply_chain:test_record", args=[k])
                    + (f"?partner={query['partner']}" if query["partner"] else ""),
                )
                for k in records.QUALITY_TEST_KINDS
            ],
            csv_url=reverse("supply_chain:tests_download") + (f"?{urlencode(here)}" if here else ""),
            counts={
                "recent": len(recent),
                "recent_failed": sum(1 for r in recent if r["result"] == "fail"),
                "failing_now": len(failing),
                "last_tested": max((r["tested_on"] for r in rows if not r["withdrawn"]), default=None),
            },
            clear_url=tests_url(partner=query["partner"]),
        )
        return context


def _partner_points(program_id, partner_org_id) -> set[int]:
    from connect_labs.supply_chain.quality.service import _points_of_partner

    return set(_points_of_partner(program_id, partner_org_id))


def _column_words(name: str) -> str:
    from connect_labs.supply_chain.quality.judge import figure_words

    return "Average (mg/L)" if name == records.TRIALS_AVERAGE_FIELD else figure_words(name)


class TestsDownloadView(OperationBase):
    """The tests on the page, every column, as a CSV a spreadsheet opens."""

    def get(self, request, *args, **kwargs):
        if not has_program_context(request):
            raise Http404("no programme selected")
        program_id = _access(request).program_id
        query = _query(request)
        rows = _tests(self.op, query)
        points = {p["id"]: p for p in self.op("supply_point_list", include_inactive=True)}
        partner_names = {p["id"]: p["name"] for p in _partners(program_id)}
        from connect_labs.supply_chain.stock.services.issues import partner_by_point

        owner = partner_by_point(program_id)
        readings = _reading_columns(rows)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="quality-tests-programme-{program_id}-{date.today().isoformat()}.csv"'
        )
        response.write("﻿")  # so a spreadsheet opens it as UTF-8
        writer = csv.writer(response)
        writer.writerow(
            [
                "Test id",
                "Type",
                "Date tested",
                "Tested by",
                "Where",
                "Partner",
                "Product",
                "Batch",
                "Sample bottle",
                "Dispenser or water point",
                "Collected by",
                "Date collected",
                *[_column_words(n) for n in readings],
                "Trials",
                *[records.STORAGE_CHECK_WORDS[n] for n in records.STORAGE_CHECKS],
                "Result",
                "Why",
                "Notes",
                "Who did the test",
                "Recorded at",
                "Withdrawn on",
                "Withdrawn by",
                "Why withdrawn",
            ]
        )
        for row in rows:
            point = points.get(row["supply_point_id"]) or {}
            storage = row["storage"]
            writer.writerow(
                [
                    row["id"],
                    KIND_WORDS.get(row["kind"], row["kind"]),
                    row["tested_on"],
                    row["tested_by"],
                    point.get("name", ""),
                    partner_names.get(owner.get(row["supply_point_id"]), ""),
                    row["commodity_slug"] or "",
                    row["batch"],
                    row["sample_ref"],
                    row["dispenser"],
                    row["collected_by"],
                    row["collected_on"] or "",
                    *[row["readings"].get(n, "") for n in readings],
                    " / ".join(row["trials"]),
                    *[("" if n not in storage else ("yes" if storage[n] else "no")) for n in records.STORAGE_CHECKS],
                    RESULT_WORDS.get(row["result"], row["result"]),
                    "; ".join(row["findings"]),
                    row["note"],
                    "we did" if row["source"] == "we_recorded" else "a partner did",
                    row["recorded_at"] or "",
                    row["withdrawn_on"] or "",
                    row["withdrawn_by"],
                    row["withdrawn_reason"],
                ]
            )
        return response


class TestRecordView(OperationFormView):
    operation = "quality_test_record"
    form_class = QualityTestForm
    submit_label = "Save the test"

    def kind(self):
        kind = self.kwargs["kind"]
        if kind not in records.QUALITY_TEST_KINDS:
            raise Http404(f"no test kind {kind!r}")
        return kind

    @property
    def title(self):
        return f"Record a test: {KIND_TITLES[self.kind()].lower()}"

    @property
    def intro(self):
        return {
            "batch": (
                "A sample from a batch kept at the store. Enter what you measured; the result is worked out "
                "against the product's specification. A failed batch shows on the Tests page and the Overview "
                "until a retest passes."
            ),
            "dispenser_sample": (
                "A sample a rider brought back from a dispenser. Enter what you measured; the result is worked out "
                "against the product's specification."
            ),
            "storage": ("How the stock is kept at the store. Answer what you checked; any “No” is a failed check."),
        }[self.kind()]

    def get_form_kwargs(self):
        from connect_labs.supply_chain.identity import person_name

        kwargs = super().get_form_kwargs()
        kwargs["kind"] = self.kind()
        user = self.request.user if getattr(self.request.user, "is_authenticated", False) else None
        kwargs["tested_by"] = person_name(user)
        return kwargs

    def breadcrumb(self, **kwargs):
        return [{"label": "Tests", "href": self.cancel_href()}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return tests_url(partner=_int(self.request.GET.get("partner")))

    def redirect_to(self, result):
        return tests_url(partner=_int(self.request.GET.get("partner")))


class TestWithdrawView(OperationFormView):
    operation = "quality_test_withdraw"
    form_class = QualityTestWithdrawForm
    title = "Withdraw a test"
    submit_label = "Withdraw it"
    danger = True
    footnote = "The test stays on the list, marked withdrawn with your name and the reason, and stops counting."

    def test(self):
        from connect_labs.supply_chain.models import QualityTest

        found = (
            QualityTest.objects.filter(pk=self.kwargs["test_id"], program_id=_access(self.request).program_id)
            .select_related("supply_point")
            .first()
        )
        if found is None:
            raise Http404(f"no test {self.kwargs['test_id']} in this programme")
        return found

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context"):
            test = self.test()
            what = test.batch or test.dispenser or test.supply_point.name
            context["intro"] = (
                f"{KIND_WORDS[test.kind]} of {what}, {day_text(test.tested_on)}, by {test.tested_by}: "
                f"{RESULT_WORDS[test.result].lower()}. Withdraw it only if it was recorded by mistake; "
                "a retest is recorded as a new test."
            )
        return context

    def fixed(self, **kwargs):
        return {"test_id": int(kwargs["test_id"])}

    def breadcrumb(self, **kwargs):
        return [{"label": "Tests", "href": tests_url()}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return tests_url()

    def redirect_to(self, result):
        return tests_url()
