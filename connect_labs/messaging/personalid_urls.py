from django.urls import path

from connect_labs.messaging import personalid_views

app_name = "messaging_personalid"

urlpatterns = [
    path("key/", personalid_views.key, name="key"),
    path("delivery/", personalid_views.delivery, name="delivery"),
    path("consent/", personalid_views.consent, name="consent"),
    path("callback/", personalid_views.callback, name="callback"),
]
