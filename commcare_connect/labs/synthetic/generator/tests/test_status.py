import random

from commcare_connect.labs.synthetic.generator.manifest import FlwPersona, MeanStddev
from commcare_connect.labs.synthetic.generator.status import decide_visit_status


def _p(flag_rate, archetype="steady"):
    return FlwPersona(
        id="x",
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=flag_rate,
    )


def test_zero_flag_rate_never_flags():
    rng = random.Random(0)
    persona = _p(0.0)
    for _ in range(200):
        s = decide_visit_status(persona=persona, has_anomaly=False, rng=rng)
        assert s.flagged is False
        assert s.status == "approved"


def test_high_flag_rate_eventually_flags():
    rng = random.Random(0)
    persona = _p(1.0)
    s = decide_visit_status(persona=persona, has_anomaly=False, rng=rng)
    assert s.flagged is True
    assert s.flag_reason  # non-empty string
    assert s.status in {"pending", "rejected"}


def test_anomaly_forces_flag_and_review():
    rng = random.Random(0)
    persona = _p(0.0)  # would never flag without anomaly
    s = decide_visit_status(persona=persona, has_anomaly=True, rng=rng)
    assert s.flagged is True
    assert s.review_status in {"pending", "rejected"}
