from django.urls import path

from connect_labs.messaging import views

app_name = "messaging"

urlpatterns = [
    path("", views.InboxView.as_view(), name="inbox"),
    path("thread/<int:thread_id>/", views.ThreadView.as_view(), name="thread"),
]
