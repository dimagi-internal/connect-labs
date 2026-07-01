from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    # SSE streaming endpoint for AI chat
    path("stream/", views.AIStreamView.as_view(), name="ai_stream"),
]
