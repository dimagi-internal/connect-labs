"""``load_hdx_cod``: a COD-AB zip becomes ``hdx`` boundary rows, without touching any other source."""

import io
import json
import zipfile

import pytest
from django.core.management.base import CommandError

from connect_labs.labs.admin_boundaries.management.commands import load_hdx_cod as cmd
from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.microplans.core.admin_boundaries import DEFAULT_COUNTRY_SOURCE_ORDER, BoundaryResolver

SQUARE = {"type": "Polygon", "coordinates": [[[8, 13], [9, 13], [9, 14], [8, 14], [8, 13]]]}


def _zip(**levels):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, features in levels.items():
            zf.writestr(f"ner_{name}.geojson", json.dumps({"type": "FeatureCollection", "features": features}))
    return buf.getvalue()


def _feature(**props):
    return {"type": "Feature", "properties": props, "geometry": SQUARE}


FULL = _zip(
    admin1=[_feature(adm1_pcode="NE005", adm1_name="Maradi", version=2, valid_on="2023-07-20")],
    admin2=[_feature(adm2_pcode="NE00501", adm2_name="Aguie", adm1_pcode="NE005", adm1_name="Maradi")],
    admin3=[
        _feature(
            adm3_pcode="NE0050101",
            adm3_name="Aguie",
            adm2_pcode="NE00501",
            adm2_name="Aguie",
            adm1_pcode="NE005",
            adm1_name="Maradi",
        ),
        _feature(adm3_name="no code"),
    ],
)


def test_levels_one_to_three_become_hdx_rows_with_codes_and_parents():
    rows = cmd.build_rows(FULL, "NER", "https://data.humdata.org/x.zip", "cod-ab-ner")
    assert [(r.admin_level, r.name, r.boundary_id) for r in rows] == [
        (1, "Maradi", "hdx-NE005"),
        (2, "Aguie", "hdx-NE00501"),
        (3, "Aguie", "hdx-NE0050101"),
    ]
    assert {r.source for r in rows} == {AdminBoundary.Source.HDX}
    assert [r.parent_boundary_id for r in rows] == ["", "hdx-NE005", "hdx-NE00501"]
    assert rows[2].extra["parent_names"] == {"state": "Maradi", "lga": "Aguie"}
    assert rows[2].extra["dataset"] == "cod-ab-ner" and rows[0].extra["place_code"] == "NE005"
    assert all(r.geometry.geom_type == "MultiPolygon" for r in rows), "a single polygon is stored as a multipolygon"


def test_a_feature_without_a_code_or_name_is_skipped_not_invented():
    rows = cmd.build_rows(FULL, "NER", "u", "d")
    assert len([r for r in rows if r.admin_level == 3]) == 1


def test_a_zip_missing_a_level_is_refused():
    with pytest.raises(CommandError, match="ner_admin3.geojson"):
        cmd.build_rows(_zip(admin1=[], admin2=[]), "NER", "u", "d")


def test_only_hdx_hosted_geojson_zip_is_followed():
    good = {
        "resources": [
            {"name": "x.shp.zip", "url": "https://data.humdata.org/a"},
            {"name": "ner_admin_boundaries.geojson.zip", "url": "https://data.humdata.org/b.zip"},
        ]
    }
    assert cmd.zip_url(good) == "https://data.humdata.org/b.zip"
    with pytest.raises(CommandError):
        cmd.zip_url({"resources": [{"name": "a.geojson.zip", "url": "https://evil.example.org/a.zip"}]})
    with pytest.raises(CommandError):
        cmd.zip_url({"resources": []})


@pytest.mark.django_db
def test_loading_is_an_upsert_that_leaves_other_sources_alone(monkeypatch):
    other = AdminBoundary.objects.create(
        iso_code="NER",
        admin_level=1,
        name="Dossa",
        boundary_id="gb-1",
        source="geoboundaries",
        geometry=cmd.build_rows(FULL, "NER", "u", "d")[0].geometry,
    )

    class Resp:
        def __init__(self, payload=None, content=b""):
            self._p, self.content = payload, content

        def raise_for_status(self):
            return self

        def json(self):
            return self._p

    class Http:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            if params:
                return Resp(
                    {
                        "result": {
                            "title": "Niger",
                            "resources": [{"name": "ner.geojson.zip", "url": "https://data.humdata.org/z.zip"}],
                        }
                    }
                )
            return Resp(content=FULL)

    monkeypatch.setattr(cmd.httpx, "Client", Http)
    for _ in range(2):  # twice: the second run updates, never duplicates
        from django.core.management import call_command

        call_command("load_hdx_cod", "NER", "cod-ab-ner")
    assert AdminBoundary.objects.filter(source="hdx").count() == 3
    assert AdminBoundary.objects.get(pk=other.pk).name == "Dossa"


def test_niger_prefers_the_cod_ab_set_and_other_countries_keep_the_default_order():
    assert DEFAULT_COUNTRY_SOURCE_ORDER["NER"][0] == "hdx"
    resolver = BoundaryResolver(sources=[])
    assert resolver._order_for("NER")[0] == "hdx"
    assert resolver._order_for("NGA")[0] == "labs"
