"""A few real data-entry slips must not set the noise on every synthetic weighing."""

import random

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm


def _pool(n_cases=200, typos=6, seed=3):
    rng = random.Random(seed)
    pool = []
    for c in range(n_cases):
        start = rng.uniform(1400, 2200)
        visits = []
        for i, day in enumerate((0, 7, 14, 21, 28)):
            w = start + 25 * day + rng.gauss(0, 30)
            visits.append({"day": day, "form": "visit", "values": {"form.w": round(w)}})
        pool.append({"owner": f"flw_{c % 10:03d}", "start_date": "2026-06-01", "visits": visits})
    # Real slips: a weight typed with an extra zero, or in kilograms.
    for c in range(typos):
        pool[c]["visits"][2]["values"]["form.w"] *= 10 if c % 2 else 0.001
    return pool


def test_residual_spread_ignores_a_few_typos():
    assert cm._robust_sd([1.0, -1.0, 2.0, -2.0, 0.0] * 20 + [50000.0, -40000.0]) < 3


def test_synthetic_weights_stay_plausible_when_the_source_has_typos():
    pool = _pool()
    out, _report = cm.synthesize_case_pool(pool, seed=11)
    weights = [v["values"]["form.w"] for case in out for v in case["visits"] if "form.w" in v.get("values", {})]
    assert weights
    implausible = sum(1 for w in weights if w < 800 or w > 5000) / len(weights)
    # The source itself is 6 slips in 1,000 weighings; the clone must stay of that order.
    assert implausible < 0.05, f"{implausible:.0%} of synthetic weights implausible"


def test_a_rare_outcome_survives_modelling():
    """child_alive flips to "no" for ~6% of babies -- under the 10% "time-varying" share --
    and a modelled cohort must still record about that many deaths (it recorded ~10%)."""
    rng = random.Random(5)
    pool = []
    for c in range(1000):
        n = rng.choice([3, 4, 5, 6, 7, 8])
        dies = rng.random() < 0.06
        visits = [
            {
                "day": i * 7,
                "form": "Register" if i == 0 else "Visit",
                "values": {"form.w": 1500 + 25 * i * 7 + rng.gauss(0, 30)},
                "cats": {"form.child_alive": "no" if (dies and i == n - 1) else "yes"},
            }
            for i in range(n)
        ]
        pool.append({"owner": f"flw_{c % 10:03d}", "start_date": "2026-06-01", "visits": visits})
    real = sum(1 for c in pool if c["visits"][-1]["cats"]["form.child_alive"] == "no")

    out, _report = cm.synthesize_case_pool(pool, seed=1)
    died = [c for c in out if any((v.get("cats") or {}).get("form.child_alive") == "no" for v in c["visits"])]

    assert 0.6 * real <= len(died) <= 1.4 * real, f"{len(died)} synthetic deaths for {real} real"
    # A death is the case's LAST visit, never stamped across earlier ones.
    for c in died:
        alive = [(v.get("cats") or {}).get("form.child_alive") for v in c["visits"]]
        assert alive[-1] == "no" and "no" not in alive[:-1]


# ---- fidelity to the source's shape (2026-10-14 batch) -------------------------------


def _kmc_like_pool(seed=7, n_cases=600):
    """A small programme with the shapes the real KMC data has: a protocol schedule that
    ends near day 28, smooth growth, workers who round to 100 g (half of them always),
    and workers who copy the birth weight into the enrolment weight."""
    rng = random.Random(seed)
    pool = []
    for c in range(n_cases):
        owner = f"flw_{c % 6:03d}"
        rounder = c % 6 < 3  # flw_000..flw_002 always round
        copier = c % 6 in (0, 3)  # flw_000 and flw_003 copy birth weight half the time
        days = [0, rng.choice([2, 3, 4]), rng.choice([7, 8]), rng.choice([13, 14, 15]), rng.choice([27, 28, 29])]
        bw = rng.uniform(1300, 2300)
        enrol = bw if (copier and rng.random() < 0.5) else bw * rng.uniform(0.88, 0.99)
        rate = rng.uniform(12, 22)  # g/kg/day
        w = enrol
        visits = []
        for i, d in enumerate(days):
            if i:
                w = w + rate * (w / 1000) * (d - days[i - 1]) + rng.gauss(0, 15)
            reading = round(w / 100) * 100 if rounder else round(w)
            values = {"form.w": float(reading)}
            if i == 0:
                values["form.bw"] = float(round(bw / 100) * 100 if rounder else round(bw))
                values["form.enrol"] = float(values["form.bw"] if enrol == bw else round(enrol))
            visits.append({"day": d, "form": "Register" if i == 0 else "Visit", "values": values})
        pool.append({"owner": owner, "start_date": f"2026-0{1 + c % 6}-{1 + c % 27:02d}", "visits": visits})
    return pool


def _shares(pool):
    spans = [c["visits"][-1]["day"] - c["visits"][0]["day"] for c in pool]
    pairs = [
        (v["values"]["form.bw"], v["values"]["form.enrol"])
        for c in pool
        for v in c["visits"]
        if "form.bw" in v["values"] and "form.enrol" in v["values"]
    ]
    impossible = 0
    for c in pool:
        pts = [(v["day"], v["values"]["form.w"]) for v in c["visits"] if "form.w" in v["values"]]
        for (d0, w0), (d1, w1) in zip(pts, pts[1:]):
            if d1 > d0 and not (-20 <= (w1 - w0) / ((w0 + w1) / 2000) / (d1 - d0) <= 45):
                impossible += 1
                break
    return {
        "span_28": sum(1 for s in spans if s >= 27) / len(spans),
        "copied": sum(1 for a, b in pairs if abs(a - b) < 1) / max(len(pairs), 1),
        "impossible": impossible / len(pool),
    }


def test_a_modelled_programme_keeps_the_sources_shape():
    """Schedule length, field copies and step plausibility all come through. Before this
    batch: span_28 fell from 100% to 78% and copying from 17% to 2%."""
    real_pool = _kmc_like_pool()
    real = _shares(real_pool)
    out, _report = cm.synthesize_case_pool(real_pool, seed=2)
    syn = _shares(out)
    assert syn["span_28"] >= real["span_28"] - 0.1, (real, syn)
    assert abs(syn["copied"] - real["copied"]) <= 0.08, (real, syn)
    assert syn["impossible"] <= real["impossible"] + 0.08, (real, syn)


def test_a_common_outcome_and_an_end_only_field_end_the_case():
    """Discharge is most cases' outcome in some programmes (60% at one KMC opportunity),
    and a discharge form's own fields are recorded only on that last visit. Both must end
    the case, never land mid-case: the opportunity's clone put 75 of 126 discharges
    mid-case (2026-10-14)."""
    rng = random.Random(9)
    pool = []
    for c in range(400):
        n = rng.choice([2, 3, 4, 5, 6])
        discharged = rng.random() < 0.6
        visits = []
        for i in range(n):
            last = i == n - 1
            cats = {
                "form.status": "enrolled" if i == 0 else ("discharged" if (discharged and last) else "in progress")
            }
            if discharged and last:
                cats["form.discontinuation.reason"] = "discharged"
            visits.append(
                {
                    "day": i * 7,
                    "form": "Register" if i == 0 else "Visit",
                    "values": {"form.w": 1800.0 + 60 * i},
                    "cats": cats,
                }
            )
        pool.append({"owner": f"flw_{c % 8:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=4)

    for path in ("form.status", "form.discontinuation.reason"):
        ended = mid = 0
        for case in out:
            vals = [(v.get("cats") or {}).get(path) for v in case["visits"]]
            hits = [i for i, x in enumerate(vals) if x == "discharged"]
            ended += bool(hits)
            mid += any(i < len(vals) - 1 for i in hits)
        assert mid == 0, f"{path}: {mid} cases discharged mid-case"
        assert 0.45 <= ended / len(out) <= 0.75, f"{path}: {ended / len(out):.0%} discharged, source 60%"


def test_a_schedule_goes_with_its_forms():
    """Two kinds of case with the same number of visits: registered on day 0 and followed
    to day 28, or first seen on a follow-up, registered later and closed early. A schedule
    drawn by length alone mixed them, and half the late-registered cases ran to day 28
    (one KMC opportunity read 60% "lost by day 28" against the source's 39%, 2026-10-14)."""
    rng = random.Random(12)
    pool = []
    for c in range(400):
        if c % 2:
            forms, days = ["Register", "Visit", "Visit", "Visit"], [0, 7, 14, rng.choice([27, 28, 29])]
        else:
            forms, days = ["Visit", "Register", "Visit", "Visit"], [0, rng.choice([9, 10, 11]), 12, 14]
        visits = [
            {"day": d, "form": f, "values": {"form.w": 1800.0 + 20 * d + rng.gauss(0, 10)}}
            for f, d in zip(forms, days)
        ]
        pool.append({"owner": f"flw_{c % 8:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=6)

    for first, short in (("Register", False), ("Visit", True)):
        spans = [
            c["visits"][-1]["day"] - c["visits"][0]["day"]
            for c in out
            if len(c["visits"]) == 4 and c["visits"][0]["form"] == first
        ]
        assert len(spans) > 50
        wrong = sum(1 for s in spans if (s > 20) == short) / len(spans)
        assert wrong < 0.05, f"{first}-first cases: {wrong:.0%} on the other kind's schedule"


def test_a_copy_held_in_two_fields_is_copied_once():
    """The birth weight held twice (two forms ask it) and copied into the enrolment weight
    in 40% of cases: each of the two pairs is equal in 40% of cases, and copying each on
    its own share made the enrolment weight a copy for ~64% of them (2026-10-14)."""
    rng = random.Random(21)
    pool = []
    for c in range(600):
        bw = float(round(rng.uniform(1300, 2300)))
        enrol = bw if rng.random() < 0.4 else float(round(bw * rng.uniform(0.88, 0.99)))
        values = {"form.bw": bw, "form.bw2": bw, "form.enrol": enrol, "form.w": enrol}
        visits = [{"day": 0, "form": "Register", "values": values}] + [
            {"day": d, "form": "Visit", "values": {"form.w": enrol + 20 * d + rng.gauss(0, 10)}} for d in (7, 14)
        ]
        pool.append({"owner": f"flw_{c % 6:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=8)

    firsts = [c["visits"][0]["values"] for c in out]
    both = [v for v in firsts if "form.bw" in v and "form.enrol" in v]
    copied = sum(1 for v in both if abs(v["form.bw"] - v["form.enrol"]) < 1) / len(both)
    assert 0.3 <= copied <= 0.5, f"{copied:.0%} copied, source 40%"


def test_readings_take_the_grid_the_source_writes_on_and_only_that():
    """Half the workers write weights in whole hundreds; the rest weigh to the gram. The
    clone keeps both habits, per worker; and a source weighed to the gram throughout,
    whose values repeat only by chance, is not put on any grid (the first attempt at
    this, snapping to any value seen in K_MIN cases, moved gram readings hundreds of
    grams onto chance repeats)."""

    def hundreds(pool, owners=None):
        vals = [
            v["values"]["form.w"]
            for c in pool
            if owners is None or c["owner"] in owners
            for v in c["visits"]
            if "form.w" in v["values"]
        ]
        return sum(1 for x in vals if x % 100 == 0) / len(vals)

    real_pool = _kmc_like_pool(n_cases=900)
    out, _report = cm.synthesize_case_pool(real_pool, seed=3)
    rounders, precise = {"flw_000", "flw_001", "flw_002"}, {"flw_003", "flw_004", "flw_005"}
    assert hundreds(out, rounders) >= 0.85, hundreds(out, rounders)
    assert hundreds(out, precise) <= 0.05, hundreds(out, precise)

    gram_pool = [c for c in _kmc_like_pool(n_cases=900) if c["owner"] in precise]
    model = cm.fit_case_model([dict(c, visits=list(c["visits"])) for c in gram_pool])
    assert not model.numeric["form.w"].grid or max(model.numeric["form.w"].grid_share.values()) < 0.02


def test_a_field_is_recorded_on_the_sources_rhythm():
    """Workers visit every two or three days but weigh only when the last weighing was
    five or more days back -- about half the visits, never two close ones. Recorded per
    form alone, the clone weighed at visits days apart (EHA: 70 weighing gaps of 2-3
    days in the first three weeks against the source's 23, 2026-10-14)."""
    rng = random.Random(4)
    pool = []
    for c in range(400):
        day, last, visits = 0, None, []
        for i in range(10):
            values = {}
            if i == 0 or day - last >= 5:
                values["form.w"] = 1800.0 + 20 * day + rng.gauss(0, 10)
                last = day
            visits.append({"day": day, "form": "Register" if i == 0 else "Visit", "values": values})
            day += rng.choice([2, 3])
        pool.append({"owner": f"flw_{c % 8:03d}", "start_date": "2026-03-02", "visits": visits})

    def close_share(cases):
        gaps = []
        for case in cases:
            days = [v["day"] for v in case["visits"] if "form.w" in v.get("values", {})]
            gaps += [b - a for a, b in zip(days, days[1:])]
        return sum(1 for g in gaps if g < 4) / len(gaps)

    out, _report = cm.synthesize_case_pool(pool, seed=5)
    assert close_share(pool) == 0
    assert close_share(out) < 0.1, f"{close_share(out):.0%} of synthetic weighings follow one under 4 days earlier"


def test_a_reading_saved_under_two_names_stays_one_reading():
    """The app saves the weight it took onto the case too: form.case.update.w is form.w
    on every visit that has it, and never without it. Modelled as two fields, each drew
    its own noise and presence, and a report reading the first one present picked up
    an independent copy -- extra, unrounded weighings with impossible steps between them
    (KMC, 2026-10-14: the copy equals the reading on 100% of the source's visits)."""
    rng = random.Random(13)
    pool = []
    for c in range(300):
        visits = []
        for i, day in enumerate((0, 7, 14, 21)):
            values = {}
            if i == 0 or rng.random() < 0.6:
                w = float(round(1800 + 20 * day + rng.gauss(0, 15)))
                values = {"form.w": w, "form.case.update.w": w}
            visits.append({"day": day, "form": "Register" if i == 0 else "Visit", "values": values})
        pool.append({"owner": f"flw_{c % 6:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=9)

    both = alone = differ = 0
    for case in out:
        for v in case["visits"]:
            vals = v.get("values", {})
            a, b = vals.get("form.w"), vals.get("form.case.update.w")
            if a is not None and b is not None:
                both += 1
                differ += a != b
            elif (a is None) != (b is None):
                alone += 1
    assert both > 300
    assert differ == 0, f"{differ} of {both} visits carry two different readings"
    assert alone == 0, f"{alone} visits record one name without the other"


def test_each_kind_of_case_keeps_its_own_schedule_length():
    """Half the cases are seen twice a few days apart; the rest five times, nearly all to
    day 28 (one in ten closes early).
    A schedule matched on a mean gap drawn across both kinds gave the long cases the
    short ones' gaps: 30% of five-visit cases ended before day 28 against 5% in the
    source (KMC EHA, 2026-10-14)."""
    rng = random.Random(17)
    pool = []
    for c in range(600):
        if c % 2:
            days = [0, rng.choice([1, 2, 3])]
        elif c % 20 == 0:  # a few five-visit cases really are short
            days = [0, 2, 4, 6, 8]
        else:
            days = [
                0,
                rng.choice([6, 7, 8]),
                rng.choice([13, 14, 15]),
                rng.choice([20, 21, 22]),
                rng.choice([28, 29, 30]),
            ]
        visits = [
            {
                "day": d,
                "form": "Register" if i == 0 else "Visit",
                "values": {"form.w": 1800.0 + 20 * d + rng.gauss(0, 10)},
            }
            for i, d in enumerate(days)
        ]
        pool.append({"owner": f"flw_{c % 6:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=12)

    long_cases = [c for c in out if len(c["visits"]) == 5]
    assert len(long_cases) > 200
    short = sum(1 for c in long_cases if c["visits"][-1]["day"] - c["visits"][0]["day"] < 27) / len(long_cases)
    assert short < 0.2, f"{short:.0%} of five-visit cases end before day 27; the source has 10%"


def test_a_second_name_is_recorded_only_on_the_forms_that_carry_it():
    """The case copy (form.case.update.referred) is the union of two form questions: the
    registration checklist's and the follow-up's. Each is the copy under another name,
    but only on its own form. Written wherever the copy was, the registration question
    turned up on every follow-up (KMC PIPN, 2026-10-14: 18% of visits against 5.6%)."""
    rng = random.Random(21)
    pool = []
    for c in range(300):
        visits = []
        for i, day in enumerate((0, 7, 14, 21)):
            form = "Register" if i == 0 else "Visit"
            cats = {}
            if rng.random() < 0.5:
                val = rng.choice(["yes", "no"])
                own = "form.reg.referred" if form == "Register" else "form.visit.referred"
                cats = {own: val, "form.case.update.referred": val}
            visits.append({"day": day, "form": form, "values": {}, "cats": cats})
        pool.append({"owner": f"flw_{c % 6:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=4)

    def share(cases, path, form):
        vs = [v for case in cases for v in case["visits"] if v.get("form") == form]
        return sum(path in (v.get("cats") or {}) for v in vs) / len(vs)

    assert share(out, "form.reg.referred", "Visit") == 0
    assert share(out, "form.visit.referred", "Register") == 0
    for path, form in (("form.reg.referred", "Register"), ("form.visit.referred", "Visit")):
        assert abs(share(out, path, form) - share(pool, path, form)) < 0.08, (path, share(out, path, form))
    for case in out:
        for v in case["visits"]:
            cats = v.get("cats") or {}
            for own in ("form.reg.referred", "form.visit.referred"):
                if own in cats:
                    assert cats[own] == cats.get("form.case.update.referred")


def test_a_slope_from_two_close_readings_is_not_taken_for_growth():
    """Babies gain about 20 g a day, and a scale reads to +-80 g. A slope fitted to two
    weighings a day apart is mostly that error (+-110 g a day), and drawn as a case's
    growth across a 40-day case it put clones at 1.3 kg and 5.7 kg where the source
    sits between 2.1 and 3.7 (KMC EHA, 2026-10-15). Each case's slope counts for what
    its readings can tell, and the scale's error is read off cases a line cannot fit
    exactly."""
    rng = random.Random(17)
    pool = []
    for c in range(400):
        start_w = rng.gauss(2000, 150)
        gain = rng.gauss(20, 4)
        if c % 2:
            days = [0, rng.choice([1, 2])]
        else:
            days = [0, 7, 14, 21, 28, 35]
        visits = [
            {"day": d, "form": "Visit", "values": {"form.w": float(round(start_w + gain * d + rng.gauss(0, 80)))}}
            for d in days
        ]
        pool.append({"owner": f"flw_{c % 8:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=6)

    def spread(cases, last_day_min):
        ws = sorted(
            v["values"]["form.w"]
            for case in cases
            if case["visits"][-1]["day"] >= last_day_min
            for v in case["visits"]
            if "form.w" in v.get("values", {}) and v["day"] >= 28
        )
        return ws[len(ws) // 10], ws[9 * len(ws) // 10]

    (r10, r90), (s10, s90) = spread(pool, 28), spread(out, 28)
    assert s10 >= r10 - 150 and s90 <= r90 + 150, f"late weights {s10:.0f}-{s90:.0f}, source {r10:.0f}-{r90:.0f}"


def test_a_data_entry_slip_is_a_slip_not_a_trajectory():
    """One weighing in fifty is a slip -- 2,500 typed as 25,000. Fitted into its case's
    line, a slip made a trajectory of it: slopes of 1,400 g a day, intercepts of -19 kg,
    and clones whose tenth of weights sat at 1.6 kg and top tenth at 9.5 kg where the
    source's ran 2.1 to 3.7 (KMC EHA, 2026-10-15). A slip is taken out of its case's
    trend and comes back as a slip, at the source's rate."""
    rng = random.Random(23)
    pool = []
    readings = slips = 0
    for c in range(400):
        start_w, gain = rng.gauss(2000, 150), rng.gauss(20, 4)
        visits = []
        for d in (0, 7, 14, 21, 28):
            w = float(round((start_w + gain * d + rng.gauss(0, 60)) / 10) * 10)
            if rng.random() < 0.02:
                w *= 10
                slips += 1
            readings += 1
            visits.append({"day": d, "form": "Visit", "values": {"form.w": w}})
        pool.append({"owner": f"flw_{c % 8:03d}", "start_date": "2026-03-02", "visits": visits})

    out, _report = cm.synthesize_case_pool(pool, seed=8)

    ws = sorted(v["values"]["form.w"] for case in out for v in case["visits"] if "form.w" in v["values"])
    wild = sum(1 for w in ws if w > 8000) / len(ws)
    plain = [w for w in ws if w <= 8000]
    assert plain[len(plain) // 10] > 1700 and plain[9 * len(plain) // 10] < 3000, (
        plain[len(plain) // 10],
        plain[9 * len(plain) // 10],
    )
    assert abs(wild - slips / readings) < 0.012, f"{wild:.1%} wild readings, source {slips / readings:.1%}"
