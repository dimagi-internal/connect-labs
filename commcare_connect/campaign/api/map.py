"""Map data for the Reporting tab's 'View map' — region coverage choropleth +
worker GPS points, served as GeoJSON. Reuses labs' AdminBoundary geometry."""
import json

from django.db.models import Count
from django.http import JsonResponse

from commcare_connect.campaign.auth.decorators import require_perm
from commcare_connect.campaign.models import Worker, WorkerCase
from commcare_connect.labs.admin_boundaries.models import AdminBoundary

# Worker GPS points are sampled to this many for the scatter (a coverage picture,
# not an exhaustive plot); the choropleth uses exact per-region counts.
MAP_POINT_CAP = 8000

KYC_COLOR = {
    "approved": "#1E7B33",
    "pending": "#C68A00",
    "review": "#3843D0",
    "rejected": "#E13019",
}


def _worker_counts_by_region(campaign):
    store = WorkerCase if campaign.commcare_domain else Worker
    rows = store.objects.filter(campaign=campaign).values("region_id").annotate(n=Count("id"))
    return {r["region_id"]: r["n"] for r in rows}


def _worker_points(campaign):
    if not campaign.commcare_domain:
        return []  # legacy Worker rows carry no GPS
    feats = []
    for wc in WorkerCase.objects.filter(campaign=campaign).order_by("worker_id")[:MAP_POINT_CAP]:
        loc = wc.properties.get("location")
        if not loc:
            continue
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [loc[0], loc[1]]},
                "properties": {"color": KYC_COLOR.get(wc.properties.get("kyc"), "#5D70D2")},
            }
        )
    return feats


@require_perm("reporting", "view")
def map_data(request):
    from commcare_connect.campaign.api.bootstrap import _select_campaign

    campaign = _select_campaign(request)
    empty = {"type": "FeatureCollection", "features": []}
    if campaign is None:
        return JsonResponse({"boundaries": empty, "workers": empty, "total_workers": 0, "points_capped": False})

    counts = _worker_counts_by_region(campaign)
    region_ids = [r for r in campaign.regions.values_list("region_id", flat=True)]
    max_count = max(counts.values(), default=1) or 1

    boundary_feats = []
    for b in AdminBoundary.objects.filter(iso_code="NGA", admin_level=1, source="geopode", boundary_id__in=region_ids):
        n = counts.get(b.boundary_id, 0)
        boundary_feats.append(
            {
                "type": "Feature",
                "geometry": json.loads(b.geometry.geojson),
                "properties": {
                    "name": b.name,
                    "region_id": b.boundary_id,
                    "workers": n,
                    "intensity": round(n / max_count, 3),
                },
            }
        )

    points = _worker_points(campaign)
    return JsonResponse(
        {
            "boundaries": {"type": "FeatureCollection", "features": boundary_feats},
            "workers": {"type": "FeatureCollection", "features": points},
            "total_workers": sum(counts.values()),
            "points_capped": len(points) >= MAP_POINT_CAP,
        }
    )
