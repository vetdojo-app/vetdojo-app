from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("management/", include("apps.management.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.core.urls")),
    path("app/", include("apps.qa.urls")),
]
