from django.contrib import admin
from django.utils.html import format_html
from ..models import *

# ==================== SECTION 3: POST APPROVAL PROCESSING ====================

class ApprovalDocumentInline(admin.TabularInline):
    model = ApprovalDocument
    extra = 1
    fields = ("document_type", "file", "uploaded_by", "is_verified", "uploaded_at")
    readonly_fields = ("uploaded_by", "uploaded_at")


class ApprovalActionInline(admin.TabularInline):
    model = ApprovalAction
    extra = 0
    fields = ("action_type", "approval_level", "actor", "remarks", "created_at")
    readonly_fields = ("actor", "created_at")
    can_delete = False


@admin.register(ApprovalForm)
class ApprovalFormAdmin(admin.ModelAdmin):
    list_display = ("form_number", "subject", "department", "amount_formatted", "status_badge", "current_approval_level", "submitted_at")
    list_filter = ("status", "department", "current_approval_level", "center", "created_at", "submitted_at")
    search_fields = ("form_number", "subject", "department__name", "submitted_by__username")
    readonly_fields = ("form_number", "created_at", "submitted_at", "approved_at", "rejected_at", "updated_at")
    inlines = [ApprovalDocumentInline, ApprovalActionInline]

    fieldsets = (
        ("Form Information", {
            "fields": ("form_number", "center", "department", "subject", "description")
        }),
        ("Financial Details", {
            "fields": ("amount", "applicable_rule")
        }),
        ("Submission", {
            "fields": ("submitted_by", "submitted_at", "created_at")
        }),
        ("Approval Flow", {
            "fields": ("status", "current_approval_level", "current_approver")
        }),
        ("Completion", {
            "fields": ("approved_at", "rejected_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def status_badge(self, obj):
        colors = {
            "approved": "#28a745",
            "rejected": "#dc3545",
            "pending_operator": "#ffc107",
            "pending_mk_sabhya": "#ff6b35",
            "pending_mk_sant": "#f7931e",
            "pending_p_rajipaswami": "#fd7e14",
            "pending_hdh_guruji": "#e83e8c",
            "revision_pending": "#17a2b8",
            "initiated": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        label = obj.get_status_display()
        return format_html(f'<span style="background-color: {color}; color: white; padding: 5px 10px; border-radius: 3px;">{label}</span>')
    status_badge.short_description = "Status"

    def amount_formatted(self, obj):
        currency_code = getattr(obj, 'currency_code', 'INR') or 'INR'
        currency_symbol = getattr(obj, 'currency_symbol', '₹') or '₹'
        if currency_code != 'INR':
            amount_inr = getattr(obj, 'amount_inr', None)
            local = f"{currency_symbol}{obj.amount:,.2f}"
            if amount_inr:
                return f"{local} (₹{amount_inr:,.2f})"
            return local
        return f"₹{obj.amount:,.2f}"
    amount_formatted.short_description = "Amount"

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("form_number", "department", "subject", "amount")
        return self.readonly_fields


@admin.register(ApprovalComment)
class ApprovalCommentAdmin(admin.ModelAdmin):
    list_display = ("form", "commented_by", "comment_preview", "visibility_badge", "created_at")
    list_filter = ("show_to_lower_levels", "created_at", "commented_by")
    search_fields = ("form__form_number", "commented_by__username", "comment_text")
    readonly_fields = ("created_at", "commented_by")
    can_delete = False

    fieldsets = (
        ("Comment Details", {
            "fields": ("form", "commented_by", "comment_text")
        }),
        ("Visibility Control", {
            "fields": ("show_to_lower_levels",),
            "description": "If checked, lower levels (including End User) can see this comment."
        }),
        ("Timestamp", {
            "fields": ("created_at",)
        }),
    )

    def comment_preview(self, obj):
        preview = obj.comment_text[:80] + "..." if len(obj.comment_text) > 80 else obj.comment_text
        return preview
    comment_preview.short_description = "Comment"

    def visibility_badge(self, obj):
        if obj.show_to_lower_levels:
            return format_html('<span style="background-color: #28a745; color: white; padding: 3px 6px; border-radius: 3px;">Visible to Lower</span>')
        return format_html('<span style="background-color: #6c757d; color: white; padding: 3px 6px; border-radius: 3px;">Upper Only</span>')
    visibility_badge.short_description = "Visibility"


@admin.register(ApprovalDocument)
class ApprovalDocumentAdmin(admin.ModelAdmin):
    list_display = ("form", "document_type", "uploaded_by", "uploaded_at", "verification_badge")
    list_filter = ("document_type", "is_verified", "uploaded_at")
    search_fields = ("form__form_number", "document_type", "uploaded_by__username")
    readonly_fields = ("uploaded_at", "uploaded_by")

    fieldsets = (
        ("Document", {
            "fields": ("form", "document_type", "file")
        }),
        ("Upload Info", {
            "fields": ("uploaded_by", "uploaded_at")
        }),
        ("Verification", {
            "fields": ("is_verified",)
        }),
    )

    def verification_badge(self, obj):
        if obj.is_verified:
            return format_html('<span style="background-color: #28a745; color: white; padding: 3px 6px; border-radius: 3px;">Verified</span>')
        return format_html('<span style="background-color: #ffc107; color: black; padding: 3px 6px; border-radius: 3px;">Pending</span>')
    verification_badge.short_description = "Status"


@admin.register(ApprovalAction)
class ApprovalActionAdmin(admin.ModelAdmin):
    list_display = ("form", "action_type_badge", "approval_level", "actor", "created_at")
    list_filter = ("action_type", "approval_level", "actor", "created_at")
    search_fields = ("form__form_number", "actor__username", "remarks")
    readonly_fields = ("created_at",)
    can_delete = False

    fieldsets = (
        ("Action", {
            "fields": ("form", "action_type", "approval_level", "actor")
        }),
        ("Remarks", {
            "fields": ("remarks",)
        }),
        ("Delegation", {
            "fields": ("delegated_to", "delegation_reason"),
            "classes": ("collapse",)
        }),
        ("Timestamp", {
            "fields": ("created_at",)
        }),
    )

    def action_type_badge(self, obj):
        colors = {
            "submitted": "#17a2b8",
            "approved": "#28a745",
            "rejected": "#dc3545",
            "revision_requested": "#ffc107",
            "delegated": "#6f42c1",
            "commented": "#007bff",
        }
        color = colors.get(obj.action_type, "#6c757d")
        label = obj.get_action_type_display()
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px;">{label}</span>')
    action_type_badge.short_description = "Type"