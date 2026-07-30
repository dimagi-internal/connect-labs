from django.urls import path

from . import views, views_dev_auth
from .api import bootstrap as bootstrap_api
from .api import demand as demand_api
from .api import demo as demo_api
from .api import eoi as eoi_api
from .api import execution as execution_api
from .api import ingest as ingest_api
from .api import orgs as orgs_api
from .api import rfp as rfp_api

app_name = "supply"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    # DEBUG-only persona switch for walkthrough capture; 403 otherwise.
    path("dev-login/", views_dev_auth.dev_login, name="dev_login"),
    path("api/bootstrap/", bootstrap_api.bootstrap, name="api_bootstrap"),
    # --- org profile ---
    path("api/org/profile/", orgs_api.profile, name="api_org_profile"),
    path("api/org/certifications/", orgs_api.certifications, name="api_org_certifications"),
    path(
        "api/org/certifications/<int:cert_id>/delete/",
        orgs_api.delete_certification,
        name="api_org_certification_delete",
    ),
    # --- EOI ---
    path("api/eoi/rounds/", eoi_api.rounds, name="api_eoi_rounds"),
    path("api/eoi/rounds/<int:round_id>/transition/", eoi_api.transition_round, name="api_eoi_round_transition"),
    path("api/eoi/submissions/", eoi_api.submissions, name="api_eoi_submissions"),
    path(
        "api/eoi/submissions/<int:submission_id>/submit/",
        eoi_api.submit_submission,
        name="api_eoi_submission_submit",
    ),
    path(
        "api/eoi/submissions/<int:submission_id>/review/",
        eoi_api.review_submission,
        name="api_eoi_submission_review",
    ),
    path("api/eoi/review-queue/", eoi_api.review_queue, name="api_eoi_review_queue"),
    path("api/registry/", eoi_api.registry, name="api_registry"),
    # --- RFPs, bids, scoring, award ---
    path("api/rfps/", rfp_api.rfps, name="api_rfps"),
    path("api/rfps/<int:rfp_id>/", rfp_api.rfp_detail, name="api_rfp_detail"),
    path("api/rfps/<int:rfp_id>/lots/", rfp_api.add_lot, name="api_rfp_add_lot"),
    path("api/rfps/<int:rfp_id>/transition/", rfp_api.transition_rfp, name="api_rfp_transition"),
    path("api/rfps/<int:rfp_id>/bid/", rfp_api.save_bid, name="api_rfp_save_bid"),
    path("api/rfps/<int:rfp_id>/bid/submit/", rfp_api.submit_bid, name="api_rfp_submit_bid"),
    path("api/rfps/<int:rfp_id>/comparison/", rfp_api.comparison, name="api_rfp_comparison"),
    path("api/lot-bids/<int:lot_bid_id>/score/", rfp_api.score_lot_bid, name="api_lot_bid_score"),
    path("api/lots/<int:lot_id>/award/", rfp_api.award_lot, name="api_lot_award"),
    # --- execution (session-authenticated) ---
    path("api/contracts/", execution_api.contracts, name="api_contracts"),
    path("api/shipments/", execution_api.create_shipment, name="api_shipment_create"),
    path("api/shipments/<int:shipment_id>/", execution_api.shipment_detail, name="api_shipment_detail"),
    path("api/shipments/<int:shipment_id>/events/", execution_api.record_event, name="api_shipment_event"),
    path("api/shipments/<int:shipment_id>/confirm/", execution_api.confirm_delivery, name="api_shipment_confirm"),
    path("api/shipments/<int:shipment_id>/checkin/", execution_api.checkin, name="api_shipment_checkin"),
    path("api/discrepancies/", execution_api.discrepancies, name="api_discrepancies"),
    path(
        "api/discrepancies/<int:discrepancy_id>/resolve/",
        execution_api.resolve_discrepancy,
        name="api_discrepancy_resolve",
    ),
    path("api/nodes/", execution_api.nodes, name="api_nodes"),
    path("api/tokens/", execution_api.api_tokens, name="api_tokens"),
    path("api/tokens/<int:token_id>/revoke/", execution_api.revoke_api_token, name="api_token_revoke"),
    # --- ingestion (bearer-token authenticated, machine-to-machine) ---
    # Demand side: the partner's signal and the centre's answer.
    path("api/signals/", demand_api.signals, name="api_signals"),
    path("api/signals/raise/", demand_api.raise_shortfall, name="api_signal_raise"),
    path("api/actions/", demand_api.action_log, name="api_actions"),
    path("api/actions/reallocate/", demand_api.reallocate, name="api_action_reallocate"),
    path("api/actions/expedite/<int:shipment_id>/", demand_api.expedite, name="api_action_expedite"),
    path("api/outcomes/", demand_api.outcomes_summary, name="api_outcomes"),
    path("api/batches/<str:batch_lot>/", demand_api.batch_drill, name="api_batch_drill"),
    # --- demo world (env-token authenticated; 404 unless SUPPLY_DEMO_RESEED_TOKEN
    #     is set). Lets a render loop put the world back between takes without a
    #     shell — every OES narrative mutates state, so take two needs take
    #     one's world back.
    path("api/demo/reseed/", demo_api.reseed, name="api_demo_reseed"),
    path("api/v1/epcis/capture/", ingest_api.epcis_capture, name="api_v1_epcis_capture"),
    path("api/v1/shipments/", ingest_api.shipments, name="api_v1_shipments"),
    path("api/v1/checkins/", ingest_api.checkins, name="api_v1_checkins"),
    path(
        "api/v1/shipments/<int:shipment_id>/events/",
        ingest_api.shipment_events,
        name="api_v1_shipment_events",
    ),
    path("", views.app_view, name="app"),
]
