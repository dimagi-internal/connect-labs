from django.urls import path

from . import views
from .api import activities as activities_api
from .api import bootstrap as bootstrap_api
from .api import microplans as microplans_api
from .api import reports as reports_api
from .api import users as users_api
from .api import workers as workers_api
from .auth import oauth_views

app_name = "campaign"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
    path("login/", oauth_views.login_page, name="login"),
    path("login/initiate/", oauth_views.oauth_initiate, name="oauth_initiate"),
    path("login/callback/", oauth_views.oauth_callback, name="oauth_callback"),
    path("logout/", oauth_views.logout_view, name="logout"),
    path("api/bootstrap/", bootstrap_api.bootstrap, name="bootstrap"),
    path("api/workers/", workers_api.workers_list, name="workers_list"),
    path("api/payments/set-status/", workers_api.pay_set_status, name="pay_set_status"),
    path("api/payments/<str:worker_id>/queue/", workers_api.pay_queue, name="pay_queue"),
    path("api/kyc/<str:worker_id>/status/", workers_api.kyc_status, name="kyc_status"),
    path("api/kyc/<str:worker_id>/resolve-duplicate/", workers_api.kyc_resolve_dupe, name="kyc_resolve_dupe"),
    path("api/kyc/<str:worker_id>/investigation/", workers_api.kyc_investigation, name="kyc_investigation"),
    path("api/activities/", activities_api.activity_create, name="activity_create"),
    path("api/activities/<str:activity_id>/sync/", activities_api.activity_sync, name="activity_sync"),
    path("api/microplans/", microplans_api.microplan_create, name="microplan_create"),
    path("api/microplans/<str:microplan_id>/", microplans_api.microplan_update, name="microplan_update"),
    path("api/microplans/<str:microplan_id>/target/", microplans_api.microplan_target, name="microplan_target"),
    path("api/microplans/<str:microplan_id>/budget/", microplans_api.microplan_budget, name="microplan_budget"),
    path("api/users/invite/", users_api.user_invite, name="user_invite"),
    path("api/users/<path:username>/role/", users_api.user_set_role, name="user_set_role"),
    path("api/users/<path:username>/status/", users_api.user_set_status, name="user_set_status"),
    path("api/reports/export/", reports_api.report_export, name="report_export"),
    path("", views.AppView.as_view(), name="app"),
]
