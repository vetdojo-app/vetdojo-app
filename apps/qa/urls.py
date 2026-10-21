from django.urls import path
from . import views

app_name = "qa"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("disclaimer/", views.disclaimer, name="disclaimer"),
    path("voice/", views.voice_dashboard, name="voice_dashboard"),
    path("voice/session/", views.voice_session, name="voice_session"),
    path("voice/query/", views.voice_query, name="voice_query"),
    path("voice/conversations/<uuid:conversation_id>/end/", views.voice_conversation_end, name="voice_conversation_end"),
    path("history/", views.history, name="history"),
    path("history/voice/<uuid:conversation_id>/", views.voice_conversation_detail, name="voice_conversation_detail"),
    path("questions/<int:pk>/", views.detail, name="detail"),
    path("questions/<int:pk>/ai-alternative/", views.ai_alternative, name="ai_alternative"),
    path("answers/<int:answer_id>/feedback/", views.feedback, name="feedback"),
]
