from django.urls import path
from . import views

app_name = "qa"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("voice/", views.voice_dashboard, name="voice_dashboard"),
    path("voice/session/", views.voice_session, name="voice_session"),
    path("voice/query/", views.voice_query, name="voice_query"),
    path("history/", views.history, name="history"),
    path("questions/<int:pk>/", views.detail, name="detail"),
    path("questions/<int:pk>/ai-alternative/", views.ai_alternative, name="ai_alternative"),
    path("answers/<int:answer_id>/feedback/", views.feedback, name="feedback"),
]
