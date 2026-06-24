from django.contrib import admin
from django.shortcuts import redirect
from django.utils.html import format_html
from django.urls import reverse

# 🟢 IMPORT THE COUNTRY MODEL FROM YOUR CORE APP
from approval_core.models import Country

# 1. Create a dummy proxy class based on the Country model
class MasterZipSyncProxy(Country):
    class Meta:
        proxy = True
        verbose_name = "⚡ One-Click Master ZIP Sync Engine"
        verbose_name_plural = "⚡ One-Click Master ZIP Sync Engine"

# 2. Register it with a custom Admin class to overwrite its click action
@admin.register(MasterZipSyncProxy)
class MasterZipSyncAdmin(admin.ModelAdmin):
    def changelist_view(self, request, extra_context=None):
        """Redirects the click immediately to your clean system sync controller view"""
        return redirect('master_zip_import')

    # Hide the standard operational button items from showing up on the grid row layout
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False