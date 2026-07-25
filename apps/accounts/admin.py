from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class VetDojoUserAdmin(UserAdmin):
    list_display = ("username", "email", "display_name", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_active", "is_superuser", "date_joined")
    search_fields = ("username", "email", "display_name", "first_name", "last_name")
    ordering = ("-date_joined",)
    fieldsets = UserAdmin.fieldsets + (("VetDojo profile", {"fields": ("display_name",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("VetDojo profile", {"fields": ("email", "display_name")}),)


admin.site.site_header = "VetDojo Administration"
admin.site.site_title = "VetDojo Admin"
admin.site.index_title = "Operations dashboard"
