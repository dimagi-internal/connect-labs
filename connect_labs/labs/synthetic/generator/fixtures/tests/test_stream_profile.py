"""stream_profile writes exactly the bytes profiler.profile returns, for messy exports too."""

import datetime as dt
import random

import pytest

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm
from connect_labs.labs.synthetic.generator.fixtures import profiler as pr
from connect_labs.labs.synthetic.generator.fixtures import stream_profile as sp
from connect_labs.labs.synthetic.generator.fixtures.manifest import ManifestValidationError

NUM = [f"form.g{g}.n{j}" for g in range(2) for j in range(4)]
SEL = [f"form.g{g}.s{j}" for g in range(2) for j in range(3)]


def _app():
    q = [{"value": "/data/" + p[5:].replace(".", "/"), "type": "Decimal", "options": []} for p in NUM]
    q += [
        {"value": "/data/" + p[5:].replace(".", "/"), "type": "Select", "options": [{"value": "yes"}, {"value": "no"}]}
        for p in SEL
    ]
    q += [{"value": "/data/notes", "type": "Text", "options": []}]
    q += [{"value": "/data/dob", "type": "Date", "options": []}]
    q += [{"value": "/data/phone", "type": "Int", "options": []}]
    q += [{"value": "/data/g0", "type": "Decimal", "options": []}]  # names a group
    q += [{"value": "/data/visit_number", "type": "Int", "options": [], "calculate": "count"}]
    return {"learn_app": None, "deliver_app": {"modules": [{"forms": [{"questions": q}]}]}}


def _set(d, path, v):
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = v


def messy_export(seed: int, cases: int = 60, workers: int = 6) -> list[dict]:
    """An export with the shapes real ones have: missing and odd values, both case-id
    styles, repeats, free text, a dotted key, a field that is sometimes a group."""
    r = random.Random(seed)
    out = []
    for c in range(cases):
        start = dt.date(2025, 3, 1) + dt.timedelta(days=r.randint(0, 120))
        w0 = r.gauss(1800, 300)
        v3 = r.random() < 0.4
        for k, day in enumerate(sorted(r.sample(range(60), r.randint(1, 6)))):
            form = {"@name": r.choice(["Registration", "Visit", "Visit", "Follow up"])}
            fj = {"form": form, "metadata": {}}
            for i, p in enumerate(NUM):
                roll = r.random()
                if roll < 0.1:
                    continue
                if roll < 0.15:
                    _set(fj, p, "")
                elif roll < 0.18:
                    _set(fj, p, "n/a")
                else:
                    _set(fj, p, str(round(w0 * (1 + 0.01 * day) / (i + 1) + r.gauss(0, 4), 1)))
            for p in SEL:
                if r.random() < 0.85:
                    _set(fj, p, r.choice(["yes", "yes", "no", "yes no"]))
            if r.random() < 0.3:
                fj["form"]["g0"] = "1.5"  # sometimes a value where it is usually a group
            fj["form"]["notes"] = r.choice(["", "ok", f"remark {r.randint(0, 10**6)}", "seen at home"])
            fj["form"]["phone"] = str(r.randint(700000000, 799999999)) if r.random() < 0.7 else ""
            fj["form"]["visit_number"] = k + 1
            fj["form"]["dob"] = (start - dt.timedelta(days=r.randint(0, 9))).isoformat()
            fj["form"]["reg_date"] = start.isoformat()
            fj["form"]["status_code"] = r.choice(["a", "b", "c"])
            fj["form"]["odd.key"] = r.choice(["x", "y"])  # a dotted key
            fj["form"]["deep"] = {"a": {"b": {"c": {"d": {"e": {"f": {"g": r.randint(0, 3)}}}}}}}
            if r.random() < 0.5:
                fj["form"]["kids"] = [{"w": str(r.randint(1, 9)), "name": f"n{r.randint(0, 999)}"}] * r.randint(0, 3)
            if v3 and k == 0:
                fj["form"]["case"] = {"@case_id": f"mother{c}"}
                fj["form"]["subcase_0"] = {"case": {"@case_id": f"baby{c}"}}
            else:
                fj["form"]["case"] = {"@case_id": f"baby{c}" if v3 else f"case{c}"}
                if r.random() < 0.5:
                    fj["form"]["subcase_0"] = {"case": {"@case_id": f"visit{c}_{k}"}}
            if r.random() < 0.6:
                fj["metadata"]["location"] = f"{-1.2 + r.gauss(0, 0.05)} {36.8 + r.gauss(0, 0.05)} 0 10"
            vd = (start + dt.timedelta(days=day)).isoformat()
            if r.random() < 0.03:
                vd = "not a date"
            flagged = r.random() < 0.1
            out.append(
                {
                    "id": len(out),
                    "username": f"user{c % workers}" if r.random() > 0.02 else "",
                    "visit_date": vd,
                    "date_created": vd + "T10:15:00" if r.random() < 0.8 else None,
                    "status": r.choice(["approved"] * 8 + ["rejected", "pending", "over_limit"]),
                    "entity_id": f"e{c}" if r.random() > 0.05 else None,
                    "flagged": flagged,
                    "flag_reason": {"flags": [["gps", "far away"]]} if flagged and r.random() < 0.7 else None,
                    "form_json": fj,
                }
            )
    return out


def _both(tmp_path, visits, **kw):
    old = pr.profile(user_visits=visits, **kw)
    vf = sp.VisitFile.write(str(tmp_path / "visits.jsonl"), visits)
    info = sp.profile_to_file(out=str(tmp_path / "m.yaml"), visits=vf, **kw)
    return old, (tmp_path / "m.yaml").read_text(), info


BASE = dict(opportunity_id=77, user_data=[], opportunity_detail={"name": "Messy"}, noise_seed=9)


@pytest.mark.parametrize("seed", range(12))
def test_the_same_bytes_with_case_timelines(tmp_path, seed):
    old, new, info = _both(tmp_path, messy_export(seed), app_structure=_app(), case_timelines=True, **BASE)
    assert info["streamed"]
    assert new == old


@pytest.mark.parametrize(
    "options",
    [
        {"app_structure": _app()},
        {"app_structure": None},
        {"app_structure": _app(), "curate": True},
        {"app_structure": _app(), "form_json_paths": ["form.g0.n0", "form.g1.n3", "form.missing"]},
        {"app_structure": _app(), "mirror": True, "curate": True},
    ],
)
def test_the_same_bytes_in_every_mode(tmp_path, options):
    old, new, info = _both(tmp_path, messy_export(5), **options, **BASE)
    assert info["streamed"]
    assert new == old


def test_the_same_bytes_sampled_across_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_PARALLEL_MIN_CASES", 0)
    visits = messy_export(3, cases=120, workers=9)
    old = pr.profile(user_visits=visits, app_structure=_app(), case_timelines=True, **BASE)
    vf = sp.VisitFile.write(str(tmp_path / "visits.jsonl"), visits)
    sp.profile_to_file(
        out=str(tmp_path / "m.yaml"), visits=vf, app_structure=_app(), case_timelines=True, processes=3, **BASE
    )
    assert (tmp_path / "m.yaml").read_text() == old


def test_a_three_case_export(tmp_path):
    old, new, _ = _both(tmp_path, messy_export(1, cases=3), app_structure=_app(), case_timelines=True, **BASE)
    assert new == old


def test_an_empty_export_is_refused_the_same_way(tmp_path):
    with pytest.raises(ManifestValidationError):
        pr.profile(user_visits=[], app_structure=_app(), **BASE)
    vf = sp.VisitFile.write(str(tmp_path / "v.jsonl"), [])
    with pytest.raises(ManifestValidationError):
        sp.profile_to_file(out=str(tmp_path / "m.yaml"), visits=vf, app_structure=_app(), **BASE)
    assert not (tmp_path / "m.yaml").exists()


def test_what_the_stream_cannot_read_falls_back_to_the_profiler(tmp_path):
    visits = messy_export(2, cases=5)
    visits[3]["form_json"] = ["not", "an", "object"]
    with pytest.raises(Exception) as old_exc:
        pr.profile(user_visits=visits, app_structure=_app(), **BASE)
    vf = sp.VisitFile.write(str(tmp_path / "visits.jsonl"), visits)
    with pytest.raises(type(old_exc.value)):
        sp.profile_to_file(out=str(tmp_path / "m.yaml"), visits=vf, app_structure=_app(), **BASE)


def test_the_visit_file_reads_back_in_order_and_by_index(tmp_path):
    visits = messy_export(4, cases=10)
    vf = sp.VisitFile.write(str(tmp_path / "v.jsonl"), visits)
    assert len(vf) == len(visits)
    assert list(vf) == list(vf) == visits
    assert vf.read([7, 2, 7]) == [visits[7], visits[2], visits[7]]
