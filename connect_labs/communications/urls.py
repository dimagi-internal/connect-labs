from django.urls import path

from . import views

app_name = "communications"

urlpatterns = [
    # Drafts and what was sent; ?kind=&about= narrows to one record (POST starts a draft about it).
    path("", views.OutboxView.as_view(), name="outbox"),
    # One communication: edit, preview each person's copy, send (a person, signed in).
    path("<int:pk>/", views.CommunicationView.as_view(), name="detail"),
]
