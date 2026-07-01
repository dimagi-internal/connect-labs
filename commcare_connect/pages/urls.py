from django.urls import path

from commcare_connect.pages import views

app_name = "pages"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
]
