from django.urls import path

from . import views
from .api import bootstrap as bootstrap_api
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
    path("api/payments/set-status/", workers_api.pay_set_status, name="pay_set_status"),
    path("api/payments/<str:worker_id>/queue/", workers_api.pay_queue, name="pay_queue"),
    path("api/kyc/<str:worker_id>/status/", workers_api.kyc_status, name="kyc_status"),
    path("api/kyc/<str:worker_id>/resolve-duplicate/", workers_api.kyc_resolve_dupe, name="kyc_resolve_dupe"),
    path("api/kyc/<str:worker_id>/investigation/", workers_api.kyc_investigation, name="kyc_investigation"),
    path("", views.AppView.as_view(), name="app"),
]
