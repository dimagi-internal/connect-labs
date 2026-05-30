"""Service-delivery GPS visualization.

A modular layer over the workflow pipeline engine that turns an opportunity's
service-delivery visits into mappable GPS points, and derives a boundary
polygon from the point cloud. Reused by the microplans setup map today and,
by design, by feature reports later.

Three units, each independently testable:
    - schema.py:  the default visit-level pipeline schema (works for any app,
                  since every CommCare submission carries form_json.metadata.location)
    - points.py:  the ServiceDeliveryPoints provider (opp_id -> GeoJSON points)
    - hull.py:    derive_boundary(points) -> GeoJSON polygon (pure geometry)
"""
