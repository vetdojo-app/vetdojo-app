from django.urls import path

from . import views

app_name = "management"
urlpatterns = [
    path("login/", views.management_login, name="login"),
    path("logout/", views.management_logout, name="logout"),
    path("password-change/", views.management_password_change, name="password_change"),
    path("", views.dashboard, name="dashboard"),
    path("sources/", views.source_list, name="source_list"),
    path("transcripts/new/", views.source_edit, {"source_kind": "youtube_transcript"}, name="transcript_create"),
    path("consultations/new/", views.source_edit, {"source_kind": "consultation"}, name="consultation_create"),
    path("sources/<int:pk>/edit/", views.source_edit, name="source_edit"),
    path("sources/<int:pk>/process/", views.queue_processing, name="process"),
    path("sources/<int:pk>/toggle/", views.toggle_source, name="toggle"),
    path("sources/<int:pk>/chunks/", views.source_chunks, name="source_chunks"),
    path("sources/<int:pk>/delete/", views.source_delete, name="source_delete"),
    path("runs/", views.run_list, name="run_list"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_edit, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("settings/", views.knowledge_settings, name="settings"),
    path("test-retrieval/", views.retrieval_test, name="retrieval_test"),
]
