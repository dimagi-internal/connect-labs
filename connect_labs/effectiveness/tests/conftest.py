import pytest

from connect_labs.effectiveness.tests.helpers import boundary, value


@pytest.fixture
def nigeria(db):
    """A country of two states side by side: Kano (high mortality, low ORS) and Lagos."""
    ng = boundary(0, "Nigeria", "NGA", 0, 0, size=2)
    kano = boundary(1, "Kano", "NGA-KN", 0, 0)
    lagos = boundary(1, "Lagos", "NGA-LA", 1, 0)
    # Area mortality is IGME's subnational model, levelled to IGME's national estimate.
    value(ng, "u5mr", 100.0, source="igme")
    value(kano, "u5mr", 150.0, source="igme_subnational")
    value(lagos, "u5mr", 50.0, source="igme_subnational")
    value(kano, "ors_coverage", 20.0)
    value(lagos, "ors_coverage", 60.0)
    value(ng, "nmr", 30.0, source="igme")
    value(kano, "nmr", 40.0, source="igme_subnational")
    return {"ng": ng, "kano": kano, "lagos": lagos}
