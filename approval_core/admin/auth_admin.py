import logging
import threading
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.contrib import messages
from import_export.admin import ImportExportModelAdmin, ImportExportMixin

# App Resource & Model Imports
from ..import_export import UserResource, UserRoleResource, UserWorkspaceResource
from ..models import UserRole, UserProfile, RegistrationWorkflowConfig
from approval_core.models import UserWorkspace, EmailNotificationTemplate
from approval_core.services import EmailNotificationService
from approval_core.notification_service import NotificationService
from approval_core.import_export import RegistrationWorkflowConfigResource
from approval_workflow.views import dispatch_password_reset_notification_async

logger = logging.getLogger(__name__)

# ==============================================================================
# 📨 ASYNC MULTI-CHANNEL ACCOUNT SECURITY DIPATCHER (DYNAMIC DATABASE TEMPLATES)
# ==============================================================================
def dispatch_account_status_notifications_async(user_id, action_type, actor_username):
    """
    Background worker thread utilizing dynamic database templates (REG-03 / REG-04).
    Renders email subjects and bodies with live user context variables.
    """
    from django.db.models import Q
    from django.conf import settings

    try:
        user = User.objects.get(id=user_id)
        user_phone = getattr(user.user_profile, 'phone', None) if hasattr(user, 'user_profile') else None
        
        notifier = NotificationService()

        # 1. Resolve exact event keys matching your database choices
        event_code = 'reg_user_activated' if action_type == 'activate' else 'reg_user_declined'
        fallback_subject = "SMVS Account Activated" if action_type == 'activate' else "SMVS Registration Declined"

        # 2. Fetch the dynamic database email template safely via icontains search
        template_matrix = EmailNotificationTemplate.objects.filter(
            Q(event_type__icontains=event_code), is_active=True
        ).first()

        # 3. Render the content if the template exists, otherwise use a safe fallback
        if template_matrix:
            assigned_role = getattr(user, 'approval_role', None)

            context_matrix = {
                'user': user,
                'user_role': assigned_role, # 💡 Injected user_role safely into the context map!
                'remarks': "Your account registration has been processed by the administrator.",
                'login_url': f"{settings.SITE_URL}/login/" if hasattr(settings, 'SITE_URL') else "/login/",
            }
            compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
            compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
        else:
            compiled_subject = fallback_subject
            compiled_body = f"Jai Swaminarayan. Your account status has been updated to {action_type}ed."

        # 📨 Channel A: Send Dynamic Database Email
        if user.email:
            notifier.send_email(user.email.strip(), compiled_subject, compiled_body)

        # 📱 Cellular Channels (SMS via TextGuru & WhatsApp via Pinbot.ai)
        if user_phone:
            if action_type == 'activate':
                notifier.send_sms(user_phone, f"Jai Swaminarayan. Your SMVS Approval account has been activated. Please log into the portal. - SMVS")
                notifier.send_dynamic_whatsapp_by_event(
                    event_type='reg_user_activated',
                    approval_form=None,
                    recipient_phone=user_phone,
                    approver_name=actor_username
                )
            elif action_type == 'decline':
                notifier.send_sms(user_phone, "Your request for an SMVS Approval account has been declined by the administrator. - SMVS")
                notifier.send_dynamic_whatsapp_by_event(
                    event_type='reg_user_declined',
                    approval_form=None,
                    recipient_phone=user_phone,
                    approver_name=actor_username
                )

    except Exception as async_err:
        logger.error(f"[ACCOUNT NOTIFY ERROR] Failed processing background status change packet: {async_err}")


# ==============================================================================
# 👥 USER PROFILE INLINE INTEGRATION
# ==============================================================================
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    max_num = 1

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        for field in formset.form.base_fields.values():
            field.required = False
        return formset


# ==============================================================================
# 🏆 CUSTOM USER ADMIN EXTENSION PANEL
# ==============================================================================
class CustomUserAdmin(ImportExportModelAdmin, UserAdmin):
    resource_classes = [UserResource]
    inlines = [UserProfileInline]
    list_display = ("username", "is_active", "email", "center", "department", "mobile", "get_groups", "last_login", "is_staff")
    list_select_related = ('user_profile', 'user_profile__center', 'user_profile__department')
    list_filter = ('is_active', 'is_staff', 'is_superuser')
    
    # 🟢 Registered automated batch actions into administrative dropdown menu rules
    actions = ['activate_and_notify_users', 'decline_and_notify_registrations']

    # 🟢 ACTION 1: BATCH ACTIVATION GATE
    @admin.action(description="🟢 Activate & Notify Selected Users (Email + SMS + WA)")
    def activate_and_notify_users(self, request, queryset):
        activated_count = 0
        actor_name = request.user.get_full_name() or request.user.username

        for user in queryset:
            if not user.is_active:
                user.is_active = True
                user.save()
                
                # Symmetrically activate connected role database records
                user_role = getattr(user, 'approval_role', None)
                if user_role:
                    user_role.is_active = True
                    user_role.status = 'active'
                    user_role.save()

                # Spawn dynamic multi-channel notifier securely on a background fire-and-forget thread
                threading.Thread(
                    target=dispatch_account_status_notifications_async,
                    args=(user.id, 'activate', actor_name)
                ).start()
                
                activated_count += 1

        self.message_user(
            request, 
            f"✓ Successfully activated {activated_count} accounts. Dynamic database notification queues dispatched.", 
            messages.SUCCESS
        )

    # 🟢 ACTION 2: BATCH DECLINE GATE
    @admin.action(description="🔴 Decline & Notify Selected Registrations (Email + SMS + WA)")
    def decline_and_notify_registrations(self, request, queryset):
        declined_count = 0
        actor_name = request.user.get_full_name() or request.user.username

        for user in queryset:
            # First spawn notification worker while user data fields are still queryable in the DB
            threading.Thread(
                target=dispatch_account_status_notifications_async,
                args=(user.id, 'decline', actor_name)
            ).start()
            
            # Symmetrically update user authorization statuses
            user_role = getattr(user, 'approval_role', None)
            if user_role:
                user_role.is_active = False
                user_role.status = 'rejected'
                user_role.save()
                
            user.is_active = False
            user.save()
            declined_count += 1

        self.message_user(
            request, 
            f"✓ Successfully declined {declined_count} registration applications.", 
            messages.WARNING
        )

    def center(self, obj):
        profile = getattr(obj, 'user_profile', None)
        return profile.center.name if profile and profile.center else "-"

    def department(self, obj):
        profile = getattr(obj, 'user_profile', None)
        return profile.department.name if profile and profile.department else "-"

    def mobile(self, obj):
        profile = getattr(obj, 'user_profile', None)
        return profile.phone if profile and profile.phone else "-"

    def get_groups(self, obj):
        return ", ".join([g.name for g in obj.groups.all()]) if obj.groups.exists() else "-"

    def get_inline_instances(self, request, obj=None):
        if obj is None:   # On the "Add User" screen
            return []     # Show NO extra profile fields
        # On the "Edit User" screen, show all assigned inlines (UserProfile)
        return super().get_inline_instances(request, obj)

    def save_model(self, request, obj, form, change):
        # Check if a new password was provided in the admin change form
        raw_password = form.cleaned_data.get('password') if form else None
        
        # Call the standard save to update the record properly
        super().save_model(request, obj, form, change)
        
        # If this is a password modification action by an admin
        if change and raw_password and not raw_password.startswith(('pbkdf2_sha256$', 'bcrypt$', 'argon2$')):
            profile = getattr(obj, 'user_profile', None)
            phone = profile.phone if profile else ""
            
            # Fire the multi-channel notification in a background thread instantly
            threading.Thread(
                target=dispatch_password_reset_notification_async,
                args=(obj.email, obj.username, raw_password, phone)
            ).start()
            
            self.message_user(request, f"✓ Password notification dispatched automatically to {obj.username}.")


# ==============================================================================
# 🛡️ USER ROLE HIERARCHICAL MANAGER PANEL
# ==============================================================================
@admin.register(UserRole)
class UserRoleAdmin(ImportExportModelAdmin):
    resource_class = UserRoleResource
    list_display = (
        "user", 
        "role_badge", 
        "get_rights_summary", 
        "department", 
        "center",             
        "is_active", \
        "mobile_number"       
    )
    list_filter = ("role", "department", "center", "is_active")
    search_fields = ("user__username", "user__email", "mobile_number")

    def get_rights_summary(self, obj):
        c_count = obj.accessible_centers.count()
        d_count = obj.accessible_departments.count()
        return f"C:{c_count} D:{d_count}"
    get_rights_summary.short_description = "Access Rights"

    def role_badge(self, obj):
        colors = {
            "admin": "#dc3545",
            "end_user": "#17a2b8",
            "operator": "#ffc107",
            "mk_sabhya": "#6c757d",
            "mk_sant": "#343a40",
            "p_rajipaswami": "#20c997",
            "hdh_guruji": "#343a40",
        }
        return format_html(
            '<span style="background:{};color:white;padding:4px 8px;border-radius:4px;">{}</span>',
            colors.get(obj.role, "#6c757d"), obj.get_role_display()
        )
    role_badge.short_description = "Role"


# ==============================================================================
# 🗺️ REGISTRATION & WORKSPACE SYSTEM CONFIGURATIONS
# ==============================================================================
@admin.register(RegistrationWorkflowConfig)
class RegistrationWorkflowConfigAdmin(ImportExportModelAdmin):
    resource_classes = [RegistrationWorkflowConfigResource]
    list_display = ('config_name', 'is_direct_registration', 'is_active', 'updated_at')
    list_filter = ('is_active', 'is_direct_registration')
    filter_horizontal = ('authorized_center_posts', 'authorized_department_posts')
    
    fieldsets = (
        (None, {
            'fields': ('config_name', 'is_active')
        }),
        ('Direct Routing Bypass Option', {
            'fields': ('is_direct_registration',),
            'description': 'Enable this to bypass all structural Sant Leader validation steps completely for everyone.'
        }),
        ('Role-Based Authorization Chain (When Bypass is Off)', {
            'fields': ('authorized_center_posts', 'authorized_department_posts'),
            'description': 'Select which specific Post Roles are dynamically allowed to verify external registrations.'
        }),
    )


@admin.register(UserWorkspace)
class UserWorkspaceAdmin(ImportExportModelAdmin):
    resource_classes = [UserWorkspaceResource]
    list_display = ('get_username', 'get_full_name', 'get_departments_list', 'updated_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    filter_horizontal = ('departments',)

    def get_username(self, obj):
        return obj.user.username
    get_username.short_description = 'Username'

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip() or obj.user.username
    get_full_name.short_description = 'Person Name'

    def get_departments_list(self, obj):
        return ", ".join([d.name for d in obj.departments.all()]) or "None"
    get_departments_list.short_description = 'Assigned Departments'


# Unregister default user layer and deploy upgraded custom reference block maps
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)