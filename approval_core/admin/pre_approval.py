from django.contrib import admin
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin
from approval_core.models import SMSTemplate, ReportPermission, ActualExpenditure, BackupRestoreLog, WhatsAppNotificationTemplate, NotificationRoutingMatrix
from ..models import (
    ApprovalLevelUser,
    ApprovalLevel,
    RuleApprovalSequence,
    ApprovalRule,
    ApprovalForm,
    ApprovalDocument,
    ApprovalAction,
    UserProfile,
)
from approval_core.import_export import (
    ApprovalRuleResource, 
    RuleApprovalSequenceResource, 
    WhatsAppNotificationTemplateResource, 
    ReportPermissionResource,
    NotificationRoutingMatrixResource,
    SMSTemplateResource,
    ApprovalLevelUserResource,
    UserProfileResource,
    ApprovalLevelResource
)

# ==================== APPROVAL LEVEL USER ADMIN ====================

class ApprovalLevelUserAdmin(ImportExportModelAdmin):
    resource_classes = [ApprovalLevelUserResource]
    list_display = ("user", "approval_level", "department_summary", "is_primary_badge", "is_active")
    list_filter = ("approval_level", "is_active", "is_primary", "created_at")
    search_fields = ("user__username", "departments__name")
    filter_horizontal = ("departments",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("User Assignment", {"fields": ("user", "approval_level")}),
        ("Department Assignment", {
            "fields": ("departments",),
            "description": "Department will be auto-selected from User Profile."
        }),
        ("Status", {"fields": ("is_primary", "is_active")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    class Media:
        js = ('admin/js/jquery.init.js', 'admin/js/force_department_move.js')

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj and hasattr(obj.user, 'userprofile'):
            profile = obj.user.userprofile
            try:
                end_user_level = ApprovalLevel.objects.get(level_number=1)
                form.base_fields['approval_level'].initial = end_user_level
            except:
                pass
            if profile.department:
                form.base_fields['departments'].initial = [profile.department.id]
        return form

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        instance = form.instance
        if hasattr(instance.user, 'userprofile'):
            profile = instance.user.userprofile
            if profile.department:
                instance.departments.clear()
                instance.departments.add(profile.department)
                instance.save()

    def department_summary(self, obj):
        if obj.departments.exists():
            depts = [d.name for d in obj.departments.all()]
            summary = ", ".join(depts[:3])
            if len(depts) > 3:
                summary += f" +{len(depts)-3}"
            return summary
        return "—"
    department_summary.short_description = "Departments"

    def is_primary_badge(self, obj):
        if obj.is_primary:
            return format_html('<span style="background-color: #dc3545; color: white; padding: 3px 6px; border-radius: 3px;">Primary</span>')
        return format_html('<span style="background-color: #6c757d; color: white; padding: 3px 6px; border-radius: 3px;">Secondary</span>')
    is_primary_badge.short_description = "Role"


# ==================== RULE APPROVAL SEQUENCE INLINE ====================

class RuleApprovalSequenceInline(admin.TabularInline):
    model = RuleApprovalSequence
    extra = 0
    fields = ("approval_level", "sequence_order", "is_mandatory", "allow_delegation", "auto_approve_if_conditions_met")
    ordering = ("sequence_order",)
    verbose_name = "Approval Level"
    verbose_name_plural = "Approval Levels"

# ==================== APPROVAL RULE ADMIN WITH IMPORT/EXPORT ====================

@admin.register(ApprovalRule)
class ApprovalRuleAdmin(ImportExportModelAdmin):
    resource_classes = [ApprovalRuleResource]
    list_display = ("rule_name", "rule_type_badge", "rule_scope_badge", "chain_type_badge", "amount_range", "sequence_count", "priority", "is_active")
    list_filter = ("rule_type", "is_active", "priority", "chain_type", "applicable_country", "created_at")
    search_fields = ("rule_name",)
    inlines = [RuleApprovalSequenceInline]
    filter_horizontal = ("applicable_departments",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Rule Information", {
            "fields": ("rule_name", "rule_type", "priority", "is_active")
        }),
        ("Amount Conditions", {
            "fields": ("min_amount", "max_amount"),
            "classes": ("collapse",),
            "description": "Amount range in INR. Leave blank for no limit."
        }),
        ("Department Conditions", {
            "fields": ("applicable_departments",),
            "classes": ("collapse",)
        }),
        ("Country-Based Routing (leave blank for existing India rules)", {
            "fields": ("applicable_country", "chain_type"),
            "description": (
                "Set 'Applicable Country' ONLY for foreign country rules. "
                "Leave blank to keep all existing India rules unchanged. "
                "Chain Type — "
                "A: Country independent chain only | "
                "B: Country operator first, then India chain | "
                "C: India chain first, country notified at end."
            )
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def rule_type_badge(self, obj):
        colors = {"amount": "#007bff", "department": "#6f42c1", "combined": "#20c997"}
        color = colors.get(obj.rule_type, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 6px; border-radius: 3px;">{}</span>',
            color, obj.get_rule_type_display()
        )
    rule_type_badge.short_description = "Type"

    def rule_scope_badge(self, obj):
        if obj.applicable_country:
            return format_html(
                '<span style="background:#6f42c1;color:white;padding:3px 8px;border-radius:4px;font-size:0.8rem;">🌍 {}</span>',
                obj.applicable_country.name
            )
        if obj.applicable_departments.exists():
            depts = ", ".join(obj.applicable_departments.values_list('name', flat=True)[:2])
            return format_html(
                '<span style="background:#28a745;color:white;padding:3px 8px;border-radius:4px;font-size:0.8rem;">🏛️ {}</span>',
                depts
            )
        return format_html('<span style="background:#6c757d;color:white;padding:3px 8px;border-radius:4px;font-size:0.8rem;">All</span>')
    rule_scope_badge.short_description = "Scope"

    def chain_type_badge(self, obj):
        colors = {
            'standard':             ('#6c757d', '—'),
            'country_independent':  ('#dc3545', 'A: Independent'),
            'country_first':        ('#fd7e14', 'B: Country First'),
            'country_notify':       ('#0dcaf0', 'C: Notify at End'),
        }
        color, label = colors.get(obj.chain_type, ('#6c757d', '—'))
        return format_html(
            '<span style="background:{};color:white;padding:3px 6px;border-radius:3px;font-size:0.8rem;">{}</span>',
            color, label
        )
    chain_type_badge.short_description = "Chain"

    def amount_range(self, obj):
        if obj.min_amount and obj.max_amount:
            return f"₹{obj.min_amount:,} - ₹{obj.max_amount:,} INR"
        elif obj.min_amount:
            return f"₹{obj.min_amount:,}+ INR"
        elif obj.max_amount:
            return f"Up to ₹{obj.max_amount:,} INR"
        return "No limit"
    amount_range.short_description = "Amount (INR)"

    def sequence_count(self, obj):
        count = obj.rule_approval_sequences.count()
        return format_html(
            '<span style="background-color: #ffc107; color: black; padding: 3px 8px; border-radius: 3px;">{}</span>',
            count
        )
    sequence_count.short_description = "Steps"


# ==================== RULE APPROVAL SEQUENCE ADMIN ====================

@admin.register(RuleApprovalSequence)
class RuleApprovalSequenceAdmin(ImportExportModelAdmin):
    """Admin interface for Rule Approval Sequence"""
    resource_classes = [RuleApprovalSequenceResource]
    list_display = (
        'rule',
        'approval_level',
        'sequence_order',
        'is_mandatory',
        'allow_delegation',
        'auto_approve_if_conditions_met'
    )
    
    list_filter = (
        'rule',
        'approval_level',
        'is_mandatory',
        'allow_delegation',
        'auto_approve_if_conditions_met'
    )
    
    search_fields = ('rule__rule_name', 'approval_level__level_name')
    ordering = ('rule', 'sequence_order')
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('rule', 'approval_level', 'sequence_order')
        }),
        ('Approval Settings', {
            'fields': ('is_mandatory', 'allow_delegation', 'auto_approve_if_conditions_met'),
            'description': 'Control mandatory approval and delegation options for this level'
        }),
    )


# ==================== USER PROFILE ADMIN ====================

@admin.register(UserProfile)
class UserProfileAdmin(ImportExportModelAdmin):
    resource_classes = [UserProfileResource]
    list_display = ("user", "phone", "center", "department")
    list_filter = ("center", "department")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("user",)

    fieldsets = (
        ("User", {"fields": ("user",)}),
        ("Assignment", {"fields": ("center", "department")}),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("user",)
        return self.readonly_fields


# ==================== APPROVAL LEVEL ADMIN (Master Levels) ====================

@admin.register(ApprovalLevel)
class ApprovalLevelAdmin(ImportExportModelAdmin):
    resource_classes = [ApprovalLevelResource]
    list_display = ("level_number", "level_name", "description", "is_active", "show_time_taken")
    list_filter = ("is_active", "show_time_taken")
    search_fields = ("level_name", "description")
    ordering = ("level_number",)
    
    fieldsets = (
        ("Level Information", {
            "fields": ("level_number", "level_name", "description", "is_active")
        }),
        ("Time Taken Settings", {
            "fields": ("show_time_taken",),
            "description": "Enable or disable 'Time Taken' display in the Approval Workflow timeline for this level."
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("level_number",)  # Prevent changing level number after creation
        return ()

    def show_time_taken(self, obj):
        if obj.show_time_taken:
            return format_html('<span style="color: green; font-weight: bold;">✅ Yes</span>')
        return format_html('<span style="color: red;">❌ No</span>')
    
    show_time_taken.short_description = "Show Time Taken"
    show_time_taken.boolean = True

# ====================== SMS Template Admin ======================
@admin.register(SMSTemplate)
class SMSTemplateAdmin(ImportExportModelAdmin):
    resource_classes = [SMSTemplateResource]
    list_display = ('template_name', 'event_type', 'approval_level', 'is_active', 'created_at')
    list_filter = ('event_type', 'approval_level', 'is_active')
    search_fields = ('template_name', 'message_text')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Template Info', {
            'fields': ('template_name', 'event_type', 'approval_level', 'is_active')
        }),
        ('Message Content', {
            'fields': ('message_text',),
            'description': 'Max 160 characters. Use variables: {form_number}, {subject}, {amount}, {approver_name}, {remarks}'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

# ==================== REGISTER APPROVAL LEVEL USER ====================

admin.site.register(ApprovalLevelUser, ApprovalLevelUserAdmin)

# ==================== REPORT PERMISSION AND ACTUAL EXP ENTRY ====================

@admin.register(ReportPermission)
class ReportPermissionAdmin(ImportExportModelAdmin):
    resource_classes = [ReportPermissionResource]
    list_display  = ['user', 'can_view_report', 'can_enter_actual_amount']
    list_filter   = ['can_view_report', 'can_enter_actual_amount']
    search_fields = ['user__username', 'user__first_name', 'user__last_name']
    filter_horizontal = ['restrict_to_centers', 'restrict_to_departments']


@admin.register(ActualExpenditure)
class ActualExpenditureAdmin(admin.ModelAdmin):
    list_display  = ['form', 'actual_amount', 'entered_by', 'entered_at']
    list_filter   = ['entered_at']
    search_fields = ['form__form_number']
    readonly_fields = ['entered_at', 'updated_at']


@admin.register(WhatsAppNotificationTemplate)
class WhatsAppNotificationTemplateAdmin(ImportExportModelAdmin):
    resource_classes = [WhatsAppNotificationTemplateResource]
    list_display = ('template_name', 'event_type', 'is_active', 'created_at')
    list_filter = ('event_type', 'is_active')
    search_fields = ('template_name', 'message_body')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Template Information', {
            'fields': ('template_name', 'event_type', 'is_active')
        }),
        ('Message Body Content', {
            'fields': ('message_body',),
            'description': 'Dynamic parameters: {form_number}, {subject}, {amount}, {approver_name}, {remarks}'
        }),
        ('Interactive Button Configuration', {
            'fields': ('button_text', 'button_url'),
            'classes': ('collapse',)
        }),
    )


@admin.register(BackupRestoreLog)
class BackupRestoreLogAdmin(admin.ModelAdmin):
    """
    ========================================================================
    📋 BACKUP & RESTORE PRODUCTION AUDIT LOG PANEL
    ========================================================================
    Renders an unalterable history list of system backups and restorations.
    """
    list_display = ('action_type', 'filename', 'file_size_kb', 'status', 'user_count_verified', 'form_count_verified', 'created_at')
    list_filter = ('action_type', 'status', 'created_at')
    search_fields = ('filename', 'log_summary')
    readonly_fields = ('action_type', 'filename', 'file_size_kb', 'executed_by', 'status', 'user_count_verified', 'form_count_verified', 'log_summary', 'created_at')
    
    # 🔒 Production Security: Prevent manual adding or changing logs via admin panel
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NotificationRoutingMatrix)
class NotificationRoutingMatrixAdmin(ImportExportModelAdmin):
    resource_classes = [NotificationRoutingMatrixResource]
    list_display = ('event_type', 'email_end_user', 'wa_end_user', 'sms_end_user')
    list_filter = ('event_type',)
    ordering = ('event_type',)
    
    fieldsets = (
        ('System Event Target Context', {
            'fields': ('event_type',)
        }),
        ('Email Dispatch Configuration: Center Posts', {
            'fields': (
                ('email_center_accountant', 'email_center', 'email_center_sant', 'email_prabhari_sant', 'email_zonal_head', 'email_end_user'),
            )
        }),
        ('Email Dispatch Configuration: Department Posts', {
            'fields': (
                ('email_department', 'email_dept_leader_sant', 'email_dept_sant', 'email_hod', 'email_mk_haribhakt', 'email_mk_sant', 'email_secretary'),
            )
        }),
        ('WhatsApp Broadcast Configuration: Center Posts', {
            'fields': (
                ('wa_center_accountant', 'wa_center', 'wa_center_sant', 'wa_prabhari_sant', 'wa_zonal_head', 'wa_end_user'),
            )
        }),
        ('WhatsApp Broadcast Configuration: Department Posts', {
            'fields': (
                ('wa_department', 'wa_dept_leader_sant', 'wa_dept_sant', 'wa_hod', 'wa_mk_haribhakt', 'wa_mk_sant', 'wa_secretary'),
            )
        }),
        ('SMS Broadcast Configuration: Center Posts', {
            'fields': (
                ('sms_center_accountant', 'sms_center', 'sms_center_sant', 'sms_prabhari_sant', 'sms_zonal_head', 'sms_end_user'),
            )
        }),
        ('SMS Broadcast Configuration: Department Posts', {
            'fields': (
                ('sms_department', 'sms_dept_leader_sant', 'sms_dept_sant', 'sms_hod', 'sms_mk_haribhakt', 'sms_mk_sant', 'sms_secretary'),
            )
        }),
    )