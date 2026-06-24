from django.contrib import admin
from django.utils.html import format_html
from ..models import *
from import_export.admin import ImportExportModelAdmin
from approval_core.import_export import EmailNotificationTemplateResource

# ==================== SECTION 4: LOGS & TEMPLATES ====================

@admin.register(EmailNotificationTemplate)
class EmailNotificationTemplateAdmin(ImportExportModelAdmin):
    resource_classes = [EmailNotificationTemplateResource]
    list_display = ("template_name", "event_type_badge", "context_model_badge", "is_active")
    list_filter = ("event_type", "context_model", "is_active", "created_at")
    search_fields = ("template_name", "subject")
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Template Info", {
            "fields": ("template_name", "event_type", "context_model", "is_active")
        }),
        ("Email Content", {
            "fields": ("subject", "body", "approval_link_text")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def event_type_badge(self, obj):
        colors = {
            "submit": "#17a2b8",
            "pending_approval": "#ffc107",
            "approved": "#28a745",
            "rejected": "#dc3545",
            "revision_requested": "#6f42c1",
        }
        color = colors.get(obj.event_type, "#6c757d")
        label = obj.get_event_type_display()
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px;">{label}</span>')
    event_type_badge.short_description = "Event"

    def context_model_badge(self, obj):
        label = obj.get_context_model_display()
        return format_html(f'<span style="background-color: #007bff; color: white; padding: 3px 6px; border-radius: 3px;">{label}</span>')
    context_model_badge.short_description = "Model"


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("form", "media_channel_badge", "notification_type_badge", "recipient_details", "status_badge", "sent_at")
    list_filter = ("media_channel", "status", "notification_type", "created_at")
    search_fields = ("form__form_number", "recipient_email", "recipient_phone", "subject")
    readonly_fields = ("created_at", "sent_at")
    can_delete = False

    fieldsets = (
        ("Notification Hierarchy", {
            "fields": ("form", "notification_type", "media_channel")
        }),
        ("Recipient Route Parameters", {
            "fields": ("recipient", "recipient_email", "recipient_phone")
        }),
        ("Message Content Body", {
            "fields": ("subject", "message"),  # Displays the raw SMS text, email code, or WhatsApp strings here!
        }),
        ("Gateway Transmission Status", {
            "fields": ("status", "is_sent", "sent_at", "error_message")
        }),
    )

    def media_channel_badge(self, obj):
        colors = {
            "email": "#6f42c1",      # Purple
            "sms": "#17a2b8",        # Cyan
            "whatsapp": "#25d366",   # Official WhatsApp Green!
        }
        color = colors.get(obj.media_channel, "#6c757d")
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px; font-weight: bold;">{obj.media_channel.upper()}</span>')
    media_channel_badge.short_description = "Channel"

    def status_badge(self, obj):
        colors = {
            "sent": "#28a745",       # Success Green
            "failed": "#dc3545",     # Danger Red
            "pending": "#ffc107",    # Warning Yellow
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px;">{obj.get_status_display()}</span>')
    status_badge.short_description = "Status"

    def recipient_details(self, obj):
        if obj.media_channel == "email":
            return obj.recipient_email or "—"
        return obj.recipient_phone or "—"
    recipient_details.short_description = "Recipient Route"

    def notification_type_badge(self, obj):
        colors = {
            "form_submitted": "#007bff",
            "pending_approval": "#20c997",
            "revision_requested": "#6f42c1",
            "rejected": "#e83e8c",
            "delegated": "#fd7e14",
        }
        color = colors.get(obj.notification_type, "#6c757d")
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px;">{obj.get_notification_type_display()}</span>')
    notification_type_badge.short_description = "Event Type"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action_badge", "user", "model_name", "description_preview", "ip_address", "created_at")
    list_filter = ("action", "model_name", "user", "created_at")
    search_fields = ("user__username", "model_name", "ip_address", "description")
    readonly_fields = ("action", "model_name", "model_id", "user", "old_values", "new_values", "ip_address", "created_at")
    can_delete = False

    fieldsets = (
        ("Action", {
            "fields": ("action", "user", "model_name", "model_id", "description")
        }),
        ("Changes", {
            "fields": ("old_values", "new_values"),
            "classes": ("collapse",)
        }),
        ("System Info", {
            "fields": ("ip_address", "created_at")
        }),
    )

    def action_badge(self, obj):
        colors = {
            "login": "#28a745",
            "logout": "#6c757d",
            "create": "#17a2b8",
            "update": "#ffc107",
            "delete": "#dc3545",
            "form_submission": "#007bff",
            "approval": "#20c997",
            "rejection": "#e83e8c",
            "email_sent": "#6f42c1",
        }
        color = colors.get(obj.action, "#6c757d")
        label = obj.get_action_display()
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px;">{label}</span>')
    action_badge.short_description = "Action"

    def description_preview(self, obj):
        if not obj.description:
            return "—"
        preview = obj.description[:40] + "..." if len(obj.description) > 40 else obj.description
        return preview
    description_preview.short_description = "Description"