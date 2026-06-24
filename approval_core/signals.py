import logging
import json
from django.db.models.signals import post_save, m2m_changed, pre_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.conf import settings
from django.forms.models import model_to_dict

from .models import (
    UserProfile, 
    ApprovalLevelUser, 
    ApprovalLevel, 
    UserRole, 
    ReportPermission, 
    Department, 
    EmailMapping,
    ApprovalRule,
    RuleApprovalSequence,
    ActualExpenditure,
    AuditLog
)

logger = logging.getLogger(__name__)

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create UserProfile when a new User is created"""
    if created:
        UserProfile.objects.get_or_create(user=instance)
        logger.info("UserProfile auto-created for user: %s", instance.username)


@receiver(post_save, sender=UserProfile)
def auto_create_user_access(sender, instance, created, **kwargs):
    """
    Automate fields synchronization from UserProfile to UserRole and ReportPermission.
    🟢 FIXED: Supports MK posts AND overlapping Sant posts (Dept Leader, Dept Sant, Prabhari Sant, Center Sant).
    """
    user = instance.user
    
    # Check EmailMappings database rules to dynamically determine correct role
    user_email = user.email.strip() if user.email else ""
    matched_mappings = EmailMapping.objects.filter(email__iexact=user_email, is_active=True)
    
    mk_departments = []
    determined_role = "end_user"
    has_special_post = False
    
    if matched_mappings.exists():
        for mapping in matched_mappings:
            post_name = mapping.post.role_name.lower() if (mapping.post and mapping.post.role_name) else ""
            
            # Define all special posts that trigger automatic workflow escalation
            is_mk_post = "mk sant" in post_name or "mk haribhakt" in post_name or "mk sabhya" in post_name
            is_sant_post = "dept leader sant" in post_name or "dept sant" in post_name or "prabhari sant" in post_name or "center sant" in post_name
            
            if is_mk_post or is_sant_post:
                has_special_post = True
                if mapping.department:
                    mk_departments.append(mapping.department)
                
                # Determine overall functional role profile assignment (Highest Tier Priority Wins)
                if "mk sant" in post_name or "dept leader sant" in post_name or "dept sant" in post_name:
                    determined_role = "mk_sant"
                elif ("mk haribhakt" in post_name or "mk sabhya" in post_name or "prabhari sant" in post_name or "center sant" in post_name) and determined_role != "mk_sant":
                    determined_role = "mk_sabhya"

        # Force their primary profile department to be their actual tracking assignment!
        if mk_departments and instance.department not in mk_departments:
            instance.department = mk_departments[0]
            instance.save(update_fields=['department']) 

    # Fallback if no mappings exist
    if not matched_mappings.exists() and instance.department and "core" in instance.department.name.lower():
        determined_role = "core_member"

    # 1. FETCH OR CREATE THE USER ROLE ENTRY ROW
    role_obj, role_created = UserRole.objects.get_or_create(user=user)
    
    # Sync details directly to UserRole field
    role_obj.mobile_number = instance.phone
    role_obj.center = instance.center
    role_obj.department = instance.department
    role_obj.role = determined_role
    role_obj.save()

    # 2. AUTO-CREATE REPORT PERMISSION & MAP BOUNDS
    perm, perm_created = ReportPermission.objects.get_or_create(
        user=user,
        defaults={'can_view_report': False}
    )
    if instance.center:
        perm.restrict_to_centers.add(instance.center)
    if instance.department:
        perm.restrict_to_departments.add(instance.department)
        
    # 3. 🌟 AUTOMATIC APPROVAL LEVEL USERS & SANT/MK WORKSPACE LOOP SYNC
    if has_special_post:
        level_num = 4 if determined_role == "mk_sant" else 2
        
        try:
            level_obj = ApprovalLevel.objects.get(level_number=level_num)
            alu_obj, alu_created = ApprovalLevelUser.objects.get_or_create(
                user=user,
                defaults={'approval_level': level_obj, 'is_active': True, 'is_primary': True}
            )
            
            if not alu_created and alu_obj.approval_level != level_obj:
                alu_obj.approval_level = level_obj
                alu_obj.save()

            # Loops and maps centers/departments ONLY for matching MK or Sant posts row-by-row
            for mapping in matched_mappings:
                current_post_name = mapping.post.role_name.lower() if (mapping.post and mapping.post.role_name) else ""
                
                # Verify criteria match for this specific iteration row
                valid_row = (
                    "mk sant" in current_post_name or 
                    "mk haribhakt" in current_post_name or 
                    "mk sabhya" in current_post_name or
                    "dept leader sant" in current_post_name or
                    "dept sant" in current_post_name or
                    "prabhari sant" in current_post_name or
                    "center sant" in current_post_name
                )
                
                if valid_row:
                    # Map Department if available on this row configuration
                    if mapping.department:
                        alu_obj.departments.add(mapping.department)
                        role_obj.accessible_departments.add(mapping.department)
                    
                    # Map Center if available on this row configuration and fields exist
                    if mapping.center:
                        if hasattr(alu_obj, 'centers'):
                            alu_obj.centers.add(mapping.center)
                        if hasattr(role_obj, 'accessible_centers'):
                            role_obj.accessible_centers.add(mapping.center)
                    
            logger.info("Automatically synchronized tracking role '%s' and linked selective entities for %s", determined_role, user.username)
            
        except ApprovalLevel.DoesNotExist:
            logger.error("Configuration Warning: ApprovalLevel %d does not exist in database.", level_num)

    logger.info("UserProfile profile synchronization pipeline completed cleanly for user: %s", user.username)


@receiver(post_save, sender=UserRole)
def automatic_workflow_level_allocator(sender, instance, created, **kwargs):
    """Handles manual dropdown modifications made straight inside Django Admin UserRole view."""
    user = instance.user
    ROLE_TO_LEVEL_MAP = {
        'operator': 1,
        'mk_sabhya': 2,
        'third_party': 3,
        'mk_sant': 4,
        'p_rajipaswami': 5,
        'hdh_guruji': 6
    }
    
    target_level_number = ROLE_TO_LEVEL_MAP.get(instance.role)
    
    if target_level_number:
        try:
            level_obj = ApprovalLevel.objects.get(level_number=target_level_number)
            alu_obj, alu_created = ApprovalLevelUser.objects.get_or_create(
                user=user,
                defaults={'approval_level': level_obj, 'is_active': True, 'is_primary': True}
            )
            
            if not alu_created and alu_obj.approval_level != level_obj:
                alu_obj.approval_level = level_obj
                alu_obj.save()

            profile = getattr(user, 'user_profile', None)
            if profile and profile.department:
                alu_obj.departments.add(profile.department)
                
            if instance.accessible_departments.exists():
                for dept in instance.accessible_departments.all():
                    alu_obj.departments.add(dept)
            
        except ApprovalLevel.DoesNotExist:
            pass
    else:
        ApprovalLevelUser.objects.filter(user=user).update(is_active=False)


@receiver(m2m_changed, sender=UserRole.accessible_departments.through)
def sync_prabhari_departments_to_workflow(sender, instance, action, pk_set, **kwargs):
    """Keeps Many-to-Many accessible administrative profiles in sync with workflow states."""
    if action == "post_add" and pk_set:
        user = instance.user
        alu_obj = ApprovalLevelUser.objects.filter(user=user, is_active=True).first()
        if alu_obj:
            for dept_id in pk_set:
                try:
                    dept = Department.objects.get(id=dept_id)
                    alu_obj.departments.add(dept)
                except Department.DoesNotExist:
                    pass


@receiver(post_save, sender=User)
def notify_user_on_activation(sender, instance, created, **kwargs):
    """Send notifications safely when an administration account status updates to active."""
    if not created and instance.is_active:
        user_role = getattr(instance, 'approval_role', None)
        if user_role and user_role.is_active:
            try:
                context = {
                    'user': instance,
                    'user_role': user_role,
                    'login_url': f"{settings.SITE_URL}/login/"
                }
                html_message = render_to_string('approval_core/emails/user_activated.html', context)
                send_mail(
                    subject="Your Account is Activated - SMVS Approval System",
                    message=f"Dear {instance.username}, your account is now active.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[instance.email],
                    html_message=html_message
                )

                if user_role.mobile_number:
                    from .notification_service import NotificationService
                    notifier = NotificationService()
                    sms_text = f"Dear {instance.username}, your SMVS account is now ACTIVE. You can now login. - SMVS"
                    notifier.send_sms(user_role.mobile_number, sms_text)
            except Exception as e:
                logger.error(f"Activation notification failed: {e}")


@receiver(post_save, sender=UserRole)
def sync_mobile_to_profile(sender, instance, **kwargs):
    """Ensure two-way safety so changes made from UserRole mirror backward onto UserProfile."""
    if instance.mobile_number:
        profile, created = UserProfile.objects.get_or_create(user=instance.user)
        if profile.phone != instance.mobile_number:
            profile.phone = instance.mobile_number
            profile.save()


# ==============================================================================
# 🔐 UNIFIED COMPONENT: INTEGRATED DATABASE ENGINE AUDITING LOOPS
# ==============================================================================

AUDIT_TARGET_MODELS = [
    User, UserProfile, UserRole, EmailMapping, 
    ApprovalRule, RuleApprovalSequence, ActualExpenditure, ReportPermission
]

def serialize_audit_value(val):
    """Helper to safely format fields for JSON fields"""
    if isinstance(val, (set, list, tuple)):
        return [str(i) for i in val]
    if hasattr(val, 'id'):
        return val.id
    return str(val)

@receiver(pre_save)
def audit_pre_save_capture(sender, instance, **kwargs):
    """Captures field states before they are committed to the database"""
    if sender in AUDIT_TARGET_MODELS and instance.pk:
        try:
            old_obj = sender.objects.get(pk=instance.pk)
            instance._old_values_cache = {k: serialize_audit_value(v) for k, v in model_to_dict(old_obj).items()}
        except Exception:
            instance._old_values_cache = {}

@receiver(post_save)
def audit_post_save_logger(sender, instance, created, **kwargs):
    """Compares values and logs changes to match your AuditLogAdmin badges"""
    if sender not in AUDIT_TARGET_MODELS:
        return

    action_type = "create" if created else "update"
    old_json = None
    new_json = None
    desc = f"New record added to {sender.__name__}."

    if not created:
        old_values = getattr(instance, '_old_values_cache', {})
        current_values = {k: serialize_audit_value(v) for k, v in model_to_dict(instance).items()}
        
        # Isolate changed fields
        changed_fields = {}
        for k, current_val in current_values.items():
            old_val = old_values.get(k)
            if old_val != current_val:
                changed_fields[k] = {"from": old_val, "to": current_val}

        # 🟢 CRITICAL SAFETY CHECK: If ONLY 'last_login' changed on the User model, skip logging!
        if sender == User and list(changed_fields.keys()) == ['last_login']:
            return  # Exit out silently because your middleware already logs the login event separately!

        if not changed_fields:
            return  # Nothing changed, skip logging

        old_json = {k: v["from"] for k, v in changed_fields.items()}
        new_json = {k: v["to"] for k, v in changed_fields.items()}
        desc = f"Updated {sender.__name__} ({getattr(instance, 'username', str(instance))}): " + \
               ", ".join([f"'{k}' ({v['from']} ➔ {v['to']})" for k, v in changed_fields.items()])

    # Determine which user record context to link
    log_user = None
    if isinstance(instance, User):
        log_user = instance
    elif hasattr(instance, 'user') and isinstance(instance.user, User):
        log_user = instance.user

    try:
        AuditLog.objects.create(
            action=action_type,
            model_name=sender.__name__,
            model_id=instance.pk,
            user=log_user,
            old_values=old_json,
            new_values=new_json,
            description=desc,
            ip_address="127.0.0.1"
        )
    except Exception as e:
        logger.error(f"Audit tracking failure: {e}")

@receiver(post_delete)
def audit_post_delete_logger(sender, instance, **kwargs):
    """Tracks explicit removal footprints across monitored system tables"""
    if sender not in AUDIT_TARGET_MODELS:
        return

    log_user = None
    if hasattr(instance, 'user') and isinstance(instance.user, User):
        log_user = instance.user

    target_name = getattr(instance, 'username', str(instance))

    try:
        AuditLog.objects.create(
            action="delete",
            model_name=sender.__name__,
            model_id=instance.pk,
            user=log_user,
            description=f"CRITICAL REMOVAL: Record '{target_name}' deleted permanently from {sender.__name__}.",
            ip_address="127.0.0.1"
        )
    except Exception as e:
        logger.error(f"Audit delete log failure: {e}")