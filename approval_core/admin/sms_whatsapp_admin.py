"""
Admin configuration for SMS and WhatsApp models
"""
from django.contrib import admin
from django.utils.html import format_html
from approval_core.models import (
    SMSTemplate,
    WhatsAppTemplate,
    NotificationLog,
    ApprovalLevelContact,
)


@admin.register(SMSTemplate)
class SMSTemplateAdmin(admin.ModelAdmin):
    list_display = ('template_name', 'event_type', 'is_active', 'created_at')
    list_filter = ('event_type', 'is_active')
    search_fields = ('template_name', 'message_text')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Template Info', {
            'fields': ('template_name', 'event_type', 'is_active')
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


@admin.register(WhatsAppTemplate)
class WhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ('template_name', 'approval_level', 'event_type', 'message_type', 'is_active', 'created_at')
    list_filter = ('approval_level', 'event_type', 'message_type', 'is_active', 'created_at')
    search_fields = ('template_name', 'message_body')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Template Information', {
            'fields': ('template_name', 'approval_level', 'event_type', 'message_type', 'is_active')
        }),
        ('Message Content', {
            'fields': ('message_body',),
        }),
        ('Button Message (Optional)', {
            'fields': ('button_text', 'button_url'),
            'description': 'Only used if message_type is "button"',
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('form', 'channel', 'recipient_phone', 'colored_status', 'colored_sms_status', 'sent_at', 'created_at')
    list_filter = ('channel', 'status', 'sms_status', 'approval_level', 'created_at')
    search_fields = ('form__form_number', 'recipient_phone', 'recipient_email')
    readonly_fields = ('created_at', 'updated_at', 'external_message_id')
    date_hierarchy = 'created_at'
        
    fieldsets = (
        ('Form Information', {
            'fields': ('form', 'approval_level')
        }),
        ('Recipient Details', {
            'fields': ('recipient', 'recipient_phone', 'recipient_email')
        }),
        ('Message Details', {
            'fields': ('channel', 'message_type', 'message_content')
        }),
        ('Status', {
            'fields': ('status', 'sms_status', 'external_message_id', 'error_message', 'sms_error')
        }),
        ('Timestamps', {
            'fields': ('sent_at', 'delivered_at', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    # 🟢 ADDED: Speeds up database loading by joining related tables in one query
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('form', 'recipient', 'approval_level')

    # 🟢 ADDED: Custom styling tags for core application status logs
    def colored_status(self, obj):
        if obj.status == 'success':
            return format_html('<span style="color: #2e7d32; font-weight: bold;">🟢 Success</span>')
        elif obj.status == 'failed':
            return format_html('<span style="color: #c62828; font-weight: bold;">🔴 Failed</span>')
        return format_html('<span style="color: #ef6c00; font-weight: bold;">🟠 Pending</span>')
    colored_status.short_description = 'WhatsApp Status'

    # 🟢 ADDED: Custom styling tags for secondary backup SMS logs
    def colored_sms_status(self, obj):
        if obj.sms_status == 'success':
            return format_html('<span style="color: #2e7d32; font-weight: bold;">🟢 Success</span>')
        elif obj.sms_status == 'failed':
            return format_html('<span style="color: #c62828; font-weight: bold;">🔴 Failed</span>')
        return format_html('<span style="color: #ef6c00; font-weight: bold;">🟠 Pending</span>')
    colored_sms_status.short_description = 'SMS Status'

    def has_add_permission(self, request):
        return False  # Notifications are created programmatically
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(ApprovalLevelContact)
class ApprovalLevelContactAdmin(admin.ModelAdmin):
    list_display = ('user', 'approval_level', 'phone_number', 'is_primary', 'is_active', 'created_at')
    list_filter = ('approval_level', 'is_primary', 'is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'phone_number')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('User & Level', {
            'fields': ('user', 'approval_level')
        }),
        ('Contact Details', {
            'fields': ('phone_number', 'is_primary', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user', 'approval_level')