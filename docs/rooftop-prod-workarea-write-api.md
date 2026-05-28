# Proposed: write API for WorkAreas on production Connect

**Status:** Draft for human review. This change lands in `dimagi/commcare-connect`
(the production platform), NOT in labs — so it is intentionally *not* auto-merged
or auto-deployed by the agent. Apply + review there.

## Why

The rooftop_surveys labs app generates a sampling frame (tiny building-as-WorkArea
pins) and needs to create WorkAreas on a Connect opportunity programmatically.
Today WorkAreas are created only via the org-admin web CSV importer
(`microplanning/views.py::WorkAreaImport`). There is no OAuth API, so labs/ACE
can't create them with the `export` token they already hold.

The export layer already has the precedent: `LabsRecordDataView` is a
`ListCreateAPIView` on `BaseDataExportView` (scope `export`). We mirror it for
work areas.

## The change (in `commcare_connect/data_export/`)

### `views.py`

Make `WorkAreaDataView` writable (add `POST`), opportunity-scoped (the existing
`check_opportunity_permission` already gates it):

```python
from rest_framework.generics import ListCreateAPIView
from django.contrib.gis.geos import GEOSGeometry
from commcare_connect.microplanning.models import SRID, WorkArea
from commcare_connect.microplanning.serializers import WorkAreaWriteSerializer  # new

class WorkAreaDataView(BaseDataExportView, ListCreateAPIView):
    # ... existing get_queryset(...) stays for GET ...

    def get_serializer_class(self):
        return WorkAreaWriteSerializer if self.request.method == "POST" else WorkAreaDataSerializer

    def create(self, request, *args, **kwargs):
        # Bulk create: body is a JSON list of work-area objects (mirrors LabsRecordDataView).
        opp_id = self.kwargs["opp_id"]
        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        objs = [WorkArea(opportunity_id=opp_id, **vd) for vd in serializer.validated_data]
        with transaction.atomic():  # all-or-nothing: a constraint failure on row N rolls back the batch
            WorkArea.objects.bulk_create(objs)
        return Response({"created": len(objs)}, status=201)
```

### `serializer.py` (or microplanning/serializers.py)

```python
class WorkAreaWriteSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=255)
    ward = serializers.SlugField(max_length=255)
    centroid = serializers.JSONField()        # GeoJSON Point {"type":"Point","coordinates":[lon,lat]}
    boundary_wkt = serializers.CharField()     # "POLYGON((...))"
    building_count = serializers.IntegerField(default=1)
    expected_visit_count = serializers.IntegerField(default=1)
    target_population = serializers.IntegerField(default=0)
    case_properties = serializers.JSONField(default=dict)

    def validate(self, attrs):
        from django.contrib.gis.geos import GEOSGeometry
        import json
        attrs["centroid"] = GEOSGeometry(json.dumps(attrs["centroid"]), srid=SRID)
        attrs["boundary"] = GEOSGeometry(attrs.pop("boundary_wkt"), srid=SRID)
        return attrs
```

### Permissions / scope

No new concept. `BaseDataExportView` already enforces `TokenHasScope` + scope
`export`, and `check_opportunity_permission(user, opp_id)` rejects opps the user
can't access. POST inherits both.

### Optional: assign + push in one call

Accept an optional `opportunity_access_id` per row; if present, set it and call
the existing `bulk_create_or_update_cases_by_work_areas(work_areas, opportunity)`
so the HQ `work-area` cases are created + owned by the FLW in the same request —
collapsing labs "create" + Connect "assign" + HQ "push" into one atom.

## Labs side (already built, in `commcare_connect/rooftop_surveys/`)

- `workarea.py::to_api_payload(...)` already emits exactly this POST body
  (`slug, ward, centroid {Point}, boundary_wkt, building_count, expected_visit_count,
  target_population, case_properties`).
- Once the endpoint exists, add a `push_to_connect` method on `RooftopDataAccess`
  that POSTs `to_api_payload(build_work_areas(frame.pins))` to
  `/export/opportunity/<id>/work_areas/`.

## Until this ships: CSV fallback

`workarea.py::to_csv_rows(...)` emits the exact column labels Connect's web
importer expects (`Area Slug, Ward, Centroid, Boundary, Building Count,
Expected Visit Count, Target Population, LGA, State`), so a frame can be pushed
today by downloading that CSV and uploading it via the microplanning web UI.
