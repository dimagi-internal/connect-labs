from django.urls import path

from . import views

app_name = "embed_example"

urlpatterns = [
    path("", views.page, name="page"),
    path("token/", views.token, name="token"),
    path(".well-known/jwks.json", views.jwks, name="jwks"),
]
