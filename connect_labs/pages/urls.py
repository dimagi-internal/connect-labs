from django.urls import path

from connect_labs.pages import views

app_name = "pages"

urlpatterns = [
    path("ping/", views.ping, name="ping"),
    path("<slug:slug>/", views.SurfacePageView.as_view(), name="surface"),
    path("<slug:slug>/card/<int:index>/data/", views.CardDataView.as_view(), name="card_data"),
]
