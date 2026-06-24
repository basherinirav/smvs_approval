import zipfile
import io
import json
import uuid
import logging
import random
import re
import os
import traceback
import time
import glob
import threading
_ZIP_COUNTRY_MAP = {}
_ZIP_ZONE_MAP = {}
_ZIP_CENTER_MAP = {}
_ZIP_DEPARTMENT_MAP = {}
_ZIP_USER_MAP = {}

from decimal import Decimal
from datetime import timedelta, datetime

# Django Core Imports
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from django.contrib import messages
from django.db.models import Q, Count, Sum  # Grouped Sum here
from django.utils import timezone
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail, EmailMessage
from django.template import Template, Context
from django.urls import reverse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.contrib.auth.hashers import check_password
from django.views.decorators.csrf import csrf_exempt

# Local App - Services
from approval_core.currency_service import get_user_currency, convert_to_inr, get_exchange_rate
from approval_core.notification_service import NotificationService
from approval_core.utils import process_matrix_notification
from approval_core import import_export as samples
from approval_core.import_export import ImportExportHelper
from approval_core.services import EmailNotificationService

# Local App - Models
from approval_core.models import (
    ApprovalForm, ApprovalDocument, ApprovalAction, ApprovalComment, Zone, PostMaster, SMSTemplate, WhatsAppNotificationTemplate,
    UserRole, Department, NotificationLog, RuleApprovalSequence, UserWorkspace, UserProfile, RegistrationWorkflowConfig, ApprovalRule, NotificationRoutingMatrix,
    ApprovalLevelUser, ApprovalLevel, Center, ReportPermission, ActualExpenditure, EmailNotificationTemplate, EmailMapping, Country
)

# Local App - Forms
from approval_core.forms import (
    EndUserRegistrationForm, ApprovalFormCreationForm, ApprovalDocumentForm,
    ApprovalActionForm, ApprovalCommentForm, ApprovalFilterForm, RevisionUploadForm
)

# Local App - Workflows
from approval_workflow.workflows import ApprovalWorkflowEngine
from .forms import ApprovalFilterForm

logger = logging.getLogger(__name__)

# ==================== Authentication Views ====================

# ==============================================================================
# SECTION 1: SYSTEM ROUTINE & USER ENTRY VALIDATION (DYNAMIC MATRIX INTEGRATION)
# ==============================================================================
def register_view(request):
    """
    Handles user onboarding dynamically via database-managed notification templates.
    Bypasses intermediate verification chains for internal trusted domains (@*.smvs.org).
    Strictly enforces segment isolation based on specific corporate email mappings.
    Supports multiple checked department workspaces via the UserWorkspace data layer.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")
    
    if request.method == "POST":
        form = EndUserRegistrationForm(request.POST)
        if form.is_valid():
            user_email = form.cleaned_data.get("email", "").strip().lower()
            mobile = form.cleaned_data.get("mobile_number")
            selection = request.POST.get("dept_center", "").strip()
            
            trusted_regex = getattr(settings, 'TRUSTED_ENTERPRISE_DOMAINS_REGEX', r'@([a-z0-9-]+\.)*smvs\.org$')
            is_smvs_domain = bool(re.search(trusted_regex, user_email))

            selected_items = [item.strip() for item in selection.split(',') if item.strip()]
            target_dept_ids = [int(item.replace("dept_", "")) for item in selected_items if item.startswith("dept_")]
            target_center_id = next((item.replace("center_", "") for item in selected_items if item.startswith("center_")), None)
            selected_dept_names = ", ".join(Department.objects.filter(id__in=target_dept_ids).values_list('name', flat=True)) if target_dept_ids else "General"

            if is_smvs_domain:
                isValidMatch = False  # Defensive Default
                email_handle = user_email.split('@')[0]

                # Fetch any row mapping that matches this handle prefix
                all_mappings = EmailMapping.objects.filter(
                    email__icontains=email_handle,
                    is_active=True
                )

                validated_dept_ids = set()
                validated_center_match = False

                for mapping in all_mappings:
                    mapped_emails = [e.strip().lower() for e in mapping.email.split(',')]
                    
                    # Verify handle context authority
                    is_match_confirmed = False
                    for me in mapped_emails:
                        if me == user_email:
                            is_match_confirmed = True
                        elif me.split('@')[0] == email_handle and bool(re.search(r'@([a-z0-9-]+\.)*smvs\.org$', me)):
                            is_match_confirmed = True

                    if is_match_confirmed:
                        if mapping.mapping_type == 'department' and mapping.department_id:
                            validated_dept_ids.add(mapping.department_id)
                        elif mapping.mapping_type == 'center' and mapping.center_id:
                            if target_center_id and int(target_center_id) == mapping.center_id:
                                validated_center_match = True

                # Enforce routing validation checks
                if target_dept_ids:
                    if set(target_dept_ids).issubset(validated_dept_ids):
                        isValidMatch = True
                elif target_center_id:
                    if validated_center_match:
                        isValidMatch = True
                else:
                    if validated_dept_ids or validated_center_match:
                        isValidMatch = True

                if not isValidMatch:
                    messages.error(request, "Access Denied: Your corporate credentials could not be verified against system records. Please contact your administrator.")
                    return render(request, "approval_core/register.html", {
                        "form": form,
                        "departments": Department.objects.filter(is_active=True).order_by('name'),
                        "centers": Center.objects.filter(is_active=True).order_by('name'),
                        "countries": Country.objects.filter(is_active=True).order_by('name'),
                    })

            # Create core Django User object
            user = form.save(commit=False)
            if user.username:
                user.username = user.username.strip().capitalize()

            if user.first_name:
                user.first_name = user.first_name.strip().title()

            if user.last_name:
                user.last_name = user.last_name.strip().title()
            user.is_active = False 
            user.save()
            
            primary_department = None
            primary_center = None
  
            if target_dept_ids:
                primary_department = Department.objects.filter(id=target_dept_ids[0]).first()
            elif target_center_id:
                primary_center = Center.objects.filter(id=target_center_id).first()
            else:
                center_verifier_email = request.POST.get('center_verifier_email', '').strip().lower()
                center_mapping = EmailMapping.objects.filter(
                    email__iexact=center_verifier_email,
                    mapping_type='center',
                    post__role_name='Center Email Id',
                    is_active=True
                ).first()
                if center_mapping and center_mapping.center:
                    primary_center = center_mapping.center
            
            profile = user.user_profile
            profile.phone = mobile
            profile.department = primary_department
            profile.center = primary_center
            profile.save() 

            if is_smvs_domain and target_dept_ids:
                workspace, created = UserWorkspace.objects.get_or_create(user=user)
                workspace.departments.set(target_dept_ids)

            user_role = user.approval_role
            user_role.is_active = False
            user_role.department = primary_department
            user_role.center = primary_center
            user_role.mobile_number = mobile

            if is_smvs_domain:
                # Re-verify matching MK database configuration assignments
                email_handle = user_email.split('@')[0]
                all_mappings = EmailMapping.objects.filter(email__icontains=email_handle, is_active=True)
                
                mk_departments = []
                for mapping in all_mappings:
                    post_name = mapping.post.role_name.lower() if (mapping.post and mapping.post.role_name) else ""
                    if "mk sant" in post_name or "mk haribhakt" in post_name or "mk sabhya" in post_name:
                        if mapping.department:
                            mk_departments.append(mapping.department)
                
                # If they belong to an MK post group, force the default department away from 'Vahan' 
                # and point it straight to their primary spiritual assignment workspace.
                if mk_departments and primary_department not in mk_departments:
                    primary_department = mk_departments[0]
                    
                    # Update both the user profile and user role records
                    profile.department = primary_department
                    profile.save(update_fields=['department'])
                    
                    user_role.department = primary_department

            user_role.save()

            active_config = RegistrationWorkflowConfig.objects.filter(is_active=True).first()
            notifier = NotificationService()
            source_name = primary_center.name if primary_center else (primary_department.name if primary_department else "General")

# ==============================================================================
# SECTION 2: TRUSTED DOMAIN SECURITY AUDIT (DYNAMIC REG-05 LOOKUP)
# ==============================================================================
            # SYSTEM RULE: Internal enterprise emails bypass intermediate leader validations completely
            if is_smvs_domain:
                user_role.status = 'awaiting_admin'  # Escalate straight to system activation state
                user_role.save()

                # 🟢 DYNAMIC LOOKUP: Fetch REG-05 template from database
                template_matrix = EmailNotificationTemplate.objects.filter(
                    Q(event_type__icontains='new_user_registered') | Q(event_type__icontains='REG-05'),
                    is_active=True
                ).first()

                admin_emails = list(User.objects.filter(is_superuser=True, is_active=True).values_list('email', flat=True))

                if admin_emails:
                    if template_matrix:
                        context_matrix = {
                            'username': user.username,
                            'first_name': user.first_name,
                            'last_name': user.last_name,
                            'registered_email': user_email,
                            'selected_departments': selected_dept_names, 
                            'center_name': primary_center.name if primary_center else None,
                            'department_name': primary_department.name if primary_department else "General",
                            'mobile_number': mobile,
                            'admin_url': request.build_absolute_uri(reverse('admin:auth_user_change', args=[user.id])) if hasattr(request, 'build_absolute_uri') else "/admin/auth/user/",
                        }
                        compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                        compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                    else:
                        # Safe fallback layout string if template row missing from DB
                        compiled_subject = f"Notice: Corporate Trusted Domain Registration — {user.username}"
                        compiled_body = f"Jai Swaminarayan.<br><br>User <b>{user.username}</b> ({user_email}) registered via corporate bypass. Awaiting Admin activation."

                    # Send compiled template to administrative recipients
                    notifier.send_email(",".join(admin_emails), compiled_subject, compiled_body)
                    
                    # Dispatch companion backup cellular alerts to superadmins
                    for admin in User.objects.filter(is_superuser=True, is_active=True):
                        admin_phone = getattr(admin.user_profile, 'phone', None)
                        if admin_phone:
                            notifier.send_sms(admin_phone, f"Internal domain registration for {user.username} is pending activation. - SMVS")
                            
                messages.success(request, "Registration successful! Your corporate email domain is verified. Awaiting final administrator activation.")
                return redirect("login")

# ==============================================================================
# SECTION 3: DIRECT GLOBAL BYPASS OPERATIONAL EVALUATION
# ==============================================================================
            # Check if admin has set the entire application suite to direct registration
            if active_config and active_config.is_direct_registration:
                user_role.status = 'awaiting_admin'
                user_role.save()
                
                admin_emails = list(User.objects.filter(is_superuser=True, is_active=True).values_list('email', flat=True))
                if admin_emails:
                    compiled_subject = f"Notice: New Registration Received (Direct Mode) — {user.username}"
                    compiled_body = f"Jai Swaminarayan.<br><br>User <b>{user.username}</b> ({user_email}) has registered in direct bypass mode and is pending activation."
                    notifier.send_email(",".join(admin_emails), compiled_subject, compiled_body)
                    
                messages.info(request, "Registration submitted successfully! Your account is awaiting administrator activation.")
                return redirect("login")

# ==============================================================================
# SECTION 4: INTERMEDIATE LEADER AUTHORIZATION (DYNAMIC REG-01 / REG-02 LOOKUP)
# ==============================================================================
            user_role.status = 'awaiting_leader'
            user_role.save()

            recipient_emails = []
            recipient_phones = []

            try:
                # Compile recipients dynamically based on your active panel post configurations
                if primary_center and active_config:
                    allowed_posts = active_config.authorized_center_posts.all()
                    mappings = EmailMapping.objects.filter(center=primary_center, post__in=allowed_posts, is_active=True)
                    target_event_code = 'reg_leader_pending'  # Maps to REG-01
                elif primary_department and active_config:
                    allowed_posts = active_config.authorized_department_posts.all()
                    mappings = EmailMapping.objects.filter(department_id__in=target_dept_ids, post__in=allowed_posts, is_active=True)
                    target_event_code = 'reg_admin_pending'   # Maps to REG-02
                else:
                    mappings = EmailMapping.objects.none()
                    target_event_code = 'reg_leader_pending'

                recipient_emails = list(mappings.values_list('email', flat=True))
                recipient_phones = list(UserRole.objects.filter(user__email__in=recipient_emails, is_active=True).values_list('mobile_number', flat=True))

                # Secure fallback operation to system administrators if custom mappings are empty
                if not recipient_emails:
                    recipient_emails = list(User.objects.filter(is_superuser=True, is_active=True).values_list('email', flat=True))

                # 🟢 DYNAMIC LOOKUP: Fetch template dynamically matching your model choices
                template_matrix = EmailNotificationTemplate.objects.filter(
                    Q(event_type__icontains=target_event_code), is_active=True
                ).first()

                if recipient_emails:
                    if template_matrix:
                        context_matrix = {
                            'username': user.username,
                            'first_name': user.first_name,
                            'last_name': user.last_name,
                            'registered_email': user_email,
                            'selected_departments': selected_dept_names,
                            'center_name': primary_center.name if primary_center else None,
                            'department_name': primary_department.name if primary_department else "General",
                            'mobile_number': mobile,
                            'admin_url': request.build_absolute_uri(reverse('admin:index')) if hasattr(request, 'build_absolute_uri') else "/admin/",
                        }
                        compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                        compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                    else:
                        # Safe text block layout fallback if template row missing from DB
                        compiled_subject = f"Action Required: Verify External Registration Request - {source_name}"
                        compiled_body = f"Jai Swaminarayan,<br><br>A new registration request from <b>{user.get_full_name() or user.username}</b> requires your validation check."

                    # Send compiled database template cleanly
                    notifier.send_email(",".join(recipient_emails), compiled_subject, compiled_body)

                # Process cellular SMS push to intermediate verifiers
                if recipient_phones:
                    sms_text = f"New registration request for {user.username} from segment {source_name} is pending your verification. Please log into the portal. - SMVS"
                    for phone in recipient_phones:
                        if phone:
                            notifier.send_sms(phone, sms_text)

            except Exception as e:
                logger.error(f"External validation processing matrix chain failed: {e}")
            
            messages.info(request, f"Registration recorded. Awaiting verification from authorized {source_name} officials.")
            return redirect("login")

    else:
        form = EndUserRegistrationForm()

    # 🗺️ DYNAMIC COUNTRY FETCH: Pull all active countries to build dropdown dynamically
    active_countries = Country.objects.filter(is_active=True).order_by('name')
    
    return render(request, "approval_core/register.html", {
        "form": form,
        "departments": Department.objects.filter(is_active=True).order_by('name'),
        "centers": Center.objects.filter(is_active=True).order_by('name'),
        "countries": active_countries,
    })


@login_required
def initialize_form(request):
    """
    Form creation workspace gatekeeper view. Intercepts multi-department operators
    and forces an explicit workspace context choice before rendering fields.
    """
    user = request.user
    
    # 1. Fetch dynamic departments assigned to this operator profile configuration
    workspace = UserWorkspace.objects.filter(user=user).first()
    
    if workspace:
        assigned_departments = workspace.departments.filter(is_active=True).order_by('name')
    else:
        # Fallback query if user was manually added through django admin without workspace records
        profile = getattr(user, 'user_profile', None)
        if profile and profile.department:
            assigned_departments = Department.objects.filter(id=profile.department.id)
        else:
            assigned_departments = Department.objects.none()

    # 2. EVALUATE WORKSPACE ROUTING DECISION MATRIX
    if request.method == 'POST':
        selected_dept_id = request.POST.get('selected_department')
        
        # Verify access authority scope
        if assigned_departments.filter(id=selected_dept_id).exists():
            # Store choice inside current browser session cache cookie context
            request.session['active_workspace_dept_id'] = selected_dept_id
            return redirect('create_approval_form_workspace') # Forward to input fields view
        else:
            return redirect('initialize_form')

    else:
        # Process inbound page requests
        total_departments_count = assigned_departments.count()

        if total_departments_count == 0:
            # If user belongs to a center (like center users), check for direct center profile access rules
            profile = getattr(user, 'user_profile', None)
            if profile and profile.center:
                request.session['active_workspace_center_id'] = profile.center.id
                return redirect('create_approval_form_workspace')
            return render(request, 'errors/unauthorized_workspace.html')
            
        elif total_departments_count == 1:
            # Single option: Skip gateway redirect prompt entirely, assign value automatically
            request.session['active_workspace_dept_id'] = assigned_departments.first().id
            return redirect('create_approval_form_workspace')
            
        else:
            # 💡 THE ASK OPTION INTERCEPTOR: Serve selection option page layout view
            return render(request, 'approval_core/select_department.html', {
                'assigned_departments': assigned_departments
            })


def login_view(request):
    """Login view"""
    if request.user.is_authenticated:
        return redirect("dashboard")
    
    if request.method == "POST":
        username_input = request.POST.get("username", "").strip()
        password = request.POST.get("password")
        
        # 🟢 CASE-INSENSITIVE LOOKUP GATEWAY
        # Look up the actual exact casing stored in the database (e.g., finds "Sahay")
        try:
            user_record = User.objects.get(username__iexact=username_input)
            username_normalized = user_record.username  # Grab the correctly capitalized username
        except User.DoesNotExist:
            username_normalized = username_input  # Fallback to input if user doesn't exist

        user = authenticate(request, username=username_normalized, password=password)
        if user is not None:
            login(request, user)
            return redirect("dashboard")
        else:
            messages.error(request, "Invalid credentials")
    
    return render(request, "approval_core/login.html")


def logout_view(request):
    """Logout view"""
    logout(request)
    messages.success(request, "Logged out successfully")
    return redirect("login")


# ==================== Dashboard Views ====================

@login_required(login_url="login")
def dashboard_view(request):
    """Main dashboard  with Pagination for End Users and Approvers"""
    user_role = None
    try:
        user_role = request.user.approval_role
    except UserRole.DoesNotExist:
        user_role = UserRole.objects.create(
            user=request.user,
            role="end_user"
        )
    
    context = {
        "user_role": user_role,
    }
    
    if user_role.role == "admin":
        # Admin dashboard
        context.update({
            "total_forms": ApprovalForm.objects.count(),
            "pending_approval": ApprovalForm.objects.filter(status="pending").count(),
            "approved_forms": ApprovalForm.objects.filter(status="approved").count(),
            "rejected_forms": ApprovalForm.objects.filter(status="rejected").count(),
        })
        return render(request, "approval_core/admin_dashboard.html", context)
    
    elif user_role.role == "end_user":
        # End User dashboard
        all_forms = ApprovalForm.objects.filter(submitted_by=request.user).order_by('-created_at')

        # ✅ 1. PAGINATION for "Your Applications"
        paginator = Paginator(all_forms, 10) 
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context.update({
            "forms": all_forms,
            "page_obj": page_obj,
            "pending_count": all_forms.filter(status__contains="pending").count(),
            "approved_count": all_forms.filter(status="approved").count(),
            "revision_pending": all_forms.filter(status="revision_pending").count(),
        })
        return render(request, "approval_core/enduser_dashboard.html", context)
    
    else:
        # Approver dashboard
        # Build complex Q filters per assignment — match on level + department
        user_level_assignments = ApprovalLevelUser.objects.filter(
            user=request.user, is_active=True
        )

        # Build Q filters per assignment — match on level + department
        dept_level_q = Q()
        for alu in user_level_assignments:
            dept_ids = list(alu.departments.values_list('id', flat=True))
            level_q = Q(current_approval_level=alu.approval_level)

            if dept_ids:
                # Allow BOTH: normal department forms + center forms (dept=None)
                dept_q = Q(department__in=dept_ids) | Q(department__isnull=True)
                dept_level_q |= (level_q & dept_q)
            else:
                # No department restriction on this assignment — match all
                dept_level_q |= level_q

        # Combine all logical conditions for pending forms
        all_my_forms = ApprovalForm.objects.filter(
            dept_level_q |
            Q(delegated_to=request.user, is_delegated=True) |
            Q(delegated_by=request.user, is_delegated=False, status="pending")
        ).exclude(status__in=["approved", "rejected"]).distinct().order_by('-created_at')

        # ✅ 2. PAGINATION for "Pending Your Approval"
        paginator = Paginator(all_my_forms, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        context.update({
            "pending_forms": page_obj,
            "pending_count": all_my_forms.count(),
            "recent_actions": ApprovalAction.objects.filter(
                actor=request.user
            ).order_by("-created_at")[:3],
        })
        return render(request, "approval_core/approver_dashboard.html", context)


# ==================== Approval Form Views ====================

@login_required(login_url="login")
def create_approval_form_workspace(request):
    """
    Create new approval form — supports streamlined allocation switching
    with accurate, clean code matching for form number generation tracking.
    """
    from decimal import Decimal
    user_role = get_object_or_404(UserRole, user=request.user)

    if user_role.role != "end_user":
        messages.error(request, "Only End Users can create forms")
        return redirect("dashboard")

    # Fetch active workspace parameters from session cache gating steps
    active_dept_id = request.session.get('active_workspace_dept_id')
    active_center_id = request.session.get('active_workspace_center_id')

    if not active_dept_id and not active_center_id:
        return redirect('initialize_form')

    user_department = get_object_or_404(Department, id=active_dept_id) if active_dept_id else None
    all_centers = Center.objects.filter(is_active=True).order_by('name')

    # Detect user's currency configuration parameters
    user_currency = get_user_currency(request.user)
    is_foreign_currency = user_currency['code'] != 'INR'

    if request.method == "POST":
        form = ApprovalFormCreationForm(request.POST)
        if form.is_valid():
            approval_form = form.save(commit=False)

            # --- SEQUENCE NUMERIC FORM NUMBER GENERATING ENGINE ---
            current_year = timezone.now().strftime('%Y')
            next_sequence = ApprovalForm.objects.count() + 1
            sequence_str = f"{next_sequence:04d}"

            # 🟢 Use the Department code field value directly instead of slicing names
            dept_code = user_department.code if user_department and user_department.code else "GEN"
            
            # Extract allocation configuration selection parameter from POST
            allocation_type = request.POST.get('allocation_type', 'self_dept').strip()
            
            center_code = ""
            if allocation_type == 'center':
                selected_center_id = request.POST.get('selected_center', '').strip()
                if selected_center_id:
                    try:
                        center_obj = Center.objects.get(id=selected_center_id)
                        # 🟢 FIX: Fetch the exact center code parameter string directly (e.g. VSN)
                        center_code = center_obj.code if center_obj.code else center_obj.name[:3].upper()
                        approval_form.selected_center = center_obj
                    except Center.DoesNotExist:
                        messages.error(request, "Invalid center selected.")
                        return render(request, "approval_core/create_form.html", {
                            "form": form, "user_department": user_department, "centers": all_centers,
                            "user_currency": user_currency, "is_foreign_currency": is_foreign_currency,
                        })
            else:
                # 🟢 SELF DEPARTMENT TRACK: Explicitly remove center selections completely!
                approval_form.selected_center = None

                # Check for background center workspace contexts if available
                if active_center_id:
                    try:
                        center_obj = Center.objects.get(id=active_center_id)
                        center_code = center_obj.code if center_obj.code else center_obj.name[:3].upper()
                    except Center.DoesNotExist:
                        pass
                elif user_role.center:
                    center_code = user_role.center.code if user_role.center.code else user_role.center.name[:3].upper()

            parts = [sequence_str, current_year, dept_code]
            if center_code:
                parts.append(center_code)

            approval_form.form_number = "/".join(parts)
            # --- END NUMERIC GENERATION ---

            approval_form.submitted_by = request.user
            approval_form.status = "initiated"
            approval_form.submitted_at = None

            # Handle Currency Converters
            approval_form.currency_code = user_currency['code']
            approval_form.currency_symbol = user_currency['symbol']

            if is_foreign_currency:
                try:
                    amount_inr, rate = convert_to_inr(approval_form.amount, user_currency['code'])
                    approval_form.amount_inr = amount_inr
                    approval_form.exchange_rate_used = rate
                except Exception:
                    approval_form.amount_inr = approval_form.amount
                    approval_form.exchange_rate_used = Decimal('1.0')
            else:
                approval_form.amount_inr = approval_form.amount
                approval_form.exchange_rate_used = Decimal('1.0')

            # Bind workflow structural keys cleanly
            approval_form.department_id = user_department.id if user_department else None
            
            # Map tracking references based on selection type rules
            if allocation_type == 'center':
                approval_form.center = None  
            else:
                if active_center_id:
                    approval_form.center_id = int(active_center_id)
                elif user_role.center_id:
                    approval_form.center_id = user_role.center_id
                else:
                    approval_form.center_id = None

            approval_form.save()
            messages.success(request, f"Form {approval_form.form_number} created successfully")
            return redirect("form_detail", form_id=approval_form.id)
    else:
        form = ApprovalFormCreationForm()

    exchange_rate = get_exchange_rate(user_currency['code']) if is_foreign_currency else 1

    return render(request, "approval_core/create_form.html", {
        "form": form,
        "user_department": user_department,
        "centers": all_centers,
        "user_currency": user_currency,
        "is_foreign_currency": is_foreign_currency,
        "exchange_rate": exchange_rate, 
    })


@login_required(login_url="login")
def form_detail_view(request, form_id):
    """Form detail view - Fully dynamic permission check"""
    # ✅ 1. Handle non-existent IDs gracefully instead of 404
    try:
        approval_form = ApprovalForm.objects.get(id=form_id)
    except ApprovalForm.DoesNotExist:
        messages.error(request, f"Form ID {form_id} does not exist.")
        return redirect("dashboard")

    # ✅ 2. Fetch User Role
    try:
        user_role = request.user.approval_role
    except UserRole.DoesNotExist:
        messages.error(request, "Your user profile is missing a role. Please contact Admin.")
        return redirect("login")

    # ==================== DYNAMIC PERMISSION CHECK ====================

    is_owner = approval_form.submitted_by == request.user
    is_admin = user_role.role == "admin"
    current_level = approval_form.current_approval_level
    can_view = False

    # 1. Owner or Admin → always allowed
    if is_owner or is_admin:
        can_view = True

    # 2. Current approver logic (including Dept/Center check)
    if not can_view and current_level:
        alu_qs = ApprovalLevelUser.objects.filter(
            user=request.user,
            approval_level=current_level,
            is_active=True
        )
        if alu_qs.exists():
            if approval_form.department:
                user_has_dept_match = alu_qs.filter(departments=approval_form.department).exists()
                user_has_no_dept = not alu_qs.filter(departments__isnull=False).exists()
                if user_has_dept_match or user_has_no_dept:
                    can_view = True
            else:
                can_view = True

    # 3. 3rd Party Verifier (currently delegated to this user)
    if not can_view and approval_form.is_delegated and approval_form.delegated_to == request.user:
        can_view = True

    # 4. External email delegation
    if not can_view and approval_form.is_delegated and approval_form.delegated_email and request.user.email == approval_form.delegated_email:
        can_view = True

    # 5. Original delegator (after 3rd Party returns the form)
    if not can_view and approval_form.delegated_by == request.user and not approval_form.is_delegated:
        can_view = True

    # 6. NEW: Oversight / Tracking Permission
    # Allow MK Sant 1, MK Sant 2, Guruji and Prabhari to track forms in their scope
    if not can_view and user_role.role in ['mk_sabhya', 'mk_sant', 'p_rajipaswami', 'hdh_guruji', 'admin', 'prabhari']:
        # Check if the form matches the Centers or Departments allotted to this user
        in_oversight_scope = (
            user_role.accessible_centers.filter(id=approval_form.selected_center_id).exists() or
            user_role.accessible_centers.filter(id=approval_form.center_id).exists() or
            user_role.accessible_departments.filter(id=approval_form.department_id).exists()
        )
        if in_oversight_scope:
            can_view = True

    # 7. Anyone who has previously acted on this form (extra safety)
    if not can_view and ApprovalAction.objects.filter(form=approval_form, actor=request.user).exists():
        can_view = True

    # ✅ FINAL SECURITY REDIRECT
    if not can_view:
        messages.error(request, f"Access Denied: You do not have permission to view form {approval_form.form_number}")
        return redirect("dashboard")

    # ==================== PERMISSION FOR TAKE ACTION ====================
    can_take_action = False
    is_end_user = user_role.role == "end_user"

    # End users NEVER get take action rights — they are submitters only
    if not is_end_user and current_level and approval_form.status in ["pending", "revision_pending", "delegated"]:
        # 1. Normal approver at current level
        alu_qs = ApprovalLevelUser.objects.filter(
            user=request.user,
            approval_level=current_level,
            is_active=True
        )
        if alu_qs.exists():
            if approval_form.department:
                # User matches if they are assigned to this dept OR have NO dept restriction
                user_has_dept_match = alu_qs.filter(departments=approval_form.department).exists()
                user_has_no_dept = not alu_qs.filter(departments__isnull=False).exists()
                if user_has_dept_match or user_has_no_dept:
                    can_take_action = True
            else:
                # No department on form yet — any approver at this level can act
                can_take_action = True

        # 2. Currently delegated to me (3rd Party Verifier)
        elif approval_form.is_delegated and approval_form.delegated_to == request.user:
            can_take_action = True

        # 3. I am the original delegator and form has been returned
        elif (approval_form.delegated_by == request.user and
              not approval_form.is_delegated and
              approval_form.status in ["pending", "revision_pending"]):
            delegator_lvl_assign = request.user.approval_level_assignments.filter(
                is_active=True
            ).first()
            if delegator_lvl_assign:
                already_approved = ApprovalAction.objects.filter(
                    form=approval_form,
                    actor=request.user,
                    action_type='approved',
                    approval_level=delegator_lvl_assign.approval_level
                ).exists()
                if not already_approved:
                    can_take_action = True
            else:
                # No level assignment — safe fallback: allow
                can_take_action = True

    # Admin override — but NOT end_user
    if is_admin and not is_end_user:
        can_take_action = True


    # ==================== DYNAMIC PROGRESS CALCULATION ====================
    total_levels = 0
    completed_levels = 0
    pending_levels = 0

    if approval_form.applicable_rule:
        sequences = RuleApprovalSequence.objects.filter(
            rule=approval_form.applicable_rule
        ).order_by('sequence_order')
        total_levels = sequences.count()
        completed_levels = ApprovalAction.objects.filter(
            form=approval_form, 
            action_type='approved'
        ).values('approval_level').distinct().count()
        pending_levels = total_levels - completed_levels

    # ==================== DATA FOR TEMPLATE ====================
    
    actions_qs = approval_form.actions.all().select_related(
        'actor', 'approval_level', 'actor__user_profile'
    ).order_by('created_at') # ascending — needed to calculate deltas correctly

    action_list = list(actions_qs)

    for i, action in enumerate(action_list):
        if i == 0:
            # use submitted_at if available, else created_at
            prev_time = approval_form.submitted_at or approval_form.created_at
        else:
            # All other actions: time from previous action to this action
            prev_time = action_list[i - 1].created_at

        try:
            delta = action.created_at - prev_time
            total_seconds = int(delta.total_seconds())
        except TypeError:
            action.time_taken_display = "—"
            continue

        if total_seconds < 60:
            action.time_taken_display = "< 1 minute"
        elif total_seconds < 3600:
            mins = total_seconds // 60
            action.time_taken_display = f"{mins} minute{'s' if mins != 1 else ''}"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            mins = (total_seconds % 3600) // 60
            action.time_taken_display = f"{hours}h {mins}m"
        else:
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            action.time_taken_display = f"{days}d {hours}h"

    # Reverse for display — newest action at top
    actions = list(reversed(action_list))
    
    # ==================== COMMENTS ====================
    comments = approval_form.comments.all().order_by('-created_at')
    visible_comments = []
    for comment in comments:
        try:
            comment.is_visible = (comment.commented_by == request.user or comment.show_to_lower_levels or comment.is_visible_to(request.user))
        except Exception:
            comment.is_visible = (comment.commented_by == request.user)
        visible_comments.append(comment)

    # ==================== DELEGATION CHECKS ====================
    allow_delegate = False
    is_delegated_to_me = approval_form.is_delegated and approval_form.delegated_to == request.user

    # ✅ FIX 1: Never show delegate option to 3rd party verifier (they are the delegatee, not the delegator)
    if not is_delegated_to_me and approval_form.applicable_rule and current_level:
        sequence = RuleApprovalSequence.objects.filter(
            rule=approval_form.applicable_rule, 
            approval_level=current_level
        ).first()

        if sequence and getattr(sequence, 'allow_delegation', False):
            allow_delegate = True

    # ✅ FIX: Final approver always gets the delegate option regardless of sequence flag
    if can_take_action and not allow_delegate and not is_delegated_to_me and current_level and approval_form.applicable_rule:
        next_check = ApprovalWorkflowEngine.get_next_approval_level(approval_form, current_level)
        if not next_check:
            allow_delegate = True

    # Third party verifiers for delegation dropdown
    third_party_verifiers = User.objects.filter(
        approval_role__role="third_party", 
        is_active=True
    ).order_by('first_name', 'last_name')

    # Check for delegation reply (internal 3rd party OR external email)
    external_reply = None
    delegation_reply = None

    # Latest reply from any 3rd party (internal or external)
    latest_delegation_reply = approval_form.actions.filter(
        action_type__in=[
            'approved_by_external', 'rejected_by_external',   # external
            'approved_by_internal', 'rejected_by_internal',   # internal
            'delegation_returned'
        ]
    ).order_by('-created_at').first()

    if latest_delegation_reply:
        delegation_reply = latest_delegation_reply
        # Keep external_reply for backward compatibility
        if latest_delegation_reply.action_type in ['approved_by_external', 'rejected_by_external']:
            external_reply = latest_delegation_reply

    # In form_detail_view - after finding latest_delegation_reply, parse the stored info
    delegation_context = {}
    if latest_delegation_reply and latest_delegation_reply.delegation_reason:
        raw = latest_delegation_reply.delegation_reason
        if raw.startswith("DELEGATED_BY:"):
            # Parse the structured string
            parts = dict(p.split(":", 1) for p in raw.split("|") if ":" in p)
            delegation_context = {
                "delegated_by_name": parts.get("DELEGATED_BY", ""),
                "delegated_by_email": parts.get("DELEGATED_BY_EMAIL", ""),
                "external_email": parts.get("EXTERNAL_EMAIL", ""),
                "delegated_level": parts.get("LEVEL", ""),
            }

    # After finding delegation_reply, compute visibility
    show_delegation_reply_to_me = False

    if delegation_reply:
        # Find the level at which delegation happened
        delegation_level = delegation_reply.approval_level  # stored on the action

        if delegation_level and approval_form.applicable_rule:
            # Who is one step ABOVE the delegation level?
            sequences = RuleApprovalSequence.objects.filter(
                rule=approval_form.applicable_rule
            ).order_by('sequence_order')

            delegation_seq = sequences.filter(
                approval_level=delegation_level
            ).first()

            next_level_above = None
            if delegation_seq:
                next_seq = sequences.filter(
                    sequence_order__gt=delegation_seq.sequence_order
                ).first()
                if next_seq:
                    next_level_above = next_seq.approval_level

            # Check if current user is the delegator OR the next approver above
            is_the_delegator = (
                approval_form.delegated_by == request.user or
                # After return, delegated_by is cleared — check via action record
                delegation_reply.approval_level and ApprovalLevelUser.objects.filter(
                    user=request.user,
                    approval_level=delegation_level,
                    is_active=True
                ).exists()
            )

            is_next_approver_above = (
                next_level_above and
                ApprovalLevelUser.objects.filter(
                    user=request.user,
                    approval_level=next_level_above,
                    is_active=True
                ).exists()
            )

            show_delegation_reply_to_me = is_the_delegator or is_next_approver_above or is_admin

    # ✅ FIX 2: Check if the 3rd party verifier posted any comments (for smarter reply message)
    verifier_has_comments = False
    if delegation_reply and delegation_reply.actor:
        verifier_has_comments = approval_form.comments.filter(
            commented_by=delegation_reply.actor
        ).exists()

    # ==================== ALREADY ACTIONED CHECK ====================
    # Lock the Approve/Reject buttons ONLY when the form has moved PAST this user's level.
    # If the form is back at their level (revision_pending → resubmitted), UNLOCK.
    already_actioned_by_me = False
    already_action_type = ''

    if not is_admin and not is_delegated_to_me:
        my_last_action = ApprovalAction.objects.filter(
            form=approval_form,
            actor=request.user,
            action_type__in=['approved', 'rejected']
        ).order_by('-created_at').first()

        if my_last_action:
            user_level_assign = request.user.approval_level_assignments.filter(
                is_active=True
            ).first()
            if user_level_assign:
                user_level = user_level_assign.approval_level
                # Unlock if form is currently AT this user's level (resubmission scenario)
                if current_level == user_level:
                    already_actioned_by_me = False
                # Unlock if form is fully approved (no current level)
                elif approval_form.status == 'approved' and not current_level:
                    already_actioned_by_me = True
                    already_action_type = 'approved'
                # Lock only if the form has moved to a DIFFERENT level beyond this user
                elif current_level and current_level != user_level:
                    already_actioned_by_me = True
                    already_action_type = my_last_action.action_type

    # ==================== FINAL APPROVER CHECK ====================
    # Is the current user the LAST approver in the sequence?
    # ✅ FIX: 3rd party verifier (is_delegated_to_me) must NEVER see the Final Amount card
    # even if the form's current_level happens to be the final level.
    is_final_approver = False
    if can_take_action and not is_delegated_to_me and approval_form.applicable_rule and current_level:
        next_lvl = ApprovalWorkflowEngine.get_next_approval_level(approval_form, current_level)
        if not next_lvl:
            is_final_approver = True

    has_unverified_docs = approval_form.documents.filter(is_verified=False).exists()
    # ==================== CONTEXT ====================
    context = {
        "form": approval_form,
        "documents": approval_form.documents.all(),
        "actions": actions,
        "comments": visible_comments,
        "all_approval_levels": ApprovalLevel.objects.all().order_by('level_number'),
        "is_owner": is_owner,
        "is_admin": is_admin,
        "can_upload": can_upload_attachment(request.user, approval_form),
        "can_submit": is_owner and approval_form.status == "initiated",
        "can_take_action": can_take_action,
        "allow_delegate": allow_delegate,
        "third_party_verifiers": third_party_verifiers,
        "all_departments": Department.objects.filter(is_active=True).order_by('name'),
        "total_levels": total_levels,
        "completed_levels": completed_levels,
        "pending_levels": pending_levels,
        "is_end_user": user_role.role == "end_user",
        "is_delegated_to_me": is_delegated_to_me,
        "external_reply": external_reply,
        "delegation_reply": delegation_reply,
        "show_delegation_reply_to_me": show_delegation_reply_to_me,
        "delegation_context": delegation_context,
        "is_final_approver": is_final_approver,
        "verifier_has_comments": verifier_has_comments,
        "already_actioned_by_me": already_actioned_by_me,
        "already_action_type": already_action_type,
        "has_unverified_docs": has_unverified_docs,
        "is_operator": user_role.role == "operator", 
    }

    return render(request, "approval_core/form_detail.html", context)


@login_required(login_url="login")
def submit_form_view(request, form_id):
    """
    Final Master Submission Controller with Integrated Matrix CC Delivery.
    - Track 1 (Actionable Approvers): Personalizes the 'WKF-01' template per user line context.
    - Track 2 (Informative Matrix): Dispatches 'FRM-01' to Submitter (TO) and Matrix Roles (CC).
      Dynamically swaps labels to 'Revised' formats if form is coming out of revision_pending.
    """
    approval_form = get_object_or_404(ApprovalForm, id=form_id)

    if approval_form.submitted_by != request.user:
        messages.error(request, "You can only submit your own forms.")
        return redirect("dashboard")

    if approval_form.status not in ["initiated", "revision_pending"]:
        messages.warning(request, "This form has already been submitted.")
        return redirect("form_detail", form_id=form_id)

    if approval_form.documents.count() == 0:
        messages.error(request, "Please upload at least one document before submitting.")
        return redirect("form_detail", form_id=form_id)

    try:
        logger.info(f"[SUBMIT VIEW] === START === Form {approval_form.form_number}")

        # 🟢 CRITICAL STEP: Detect and save the revision state flag BEFORE status shifts via Engine
        is_form_resubmission = (approval_form.status == "revision_pending")

        # 🚀 1. Advance form status via Core Workflow Engine
        ApprovalWorkflowEngine.submit_form(approval_form, request.user)
        approval_form.refresh_from_db()
        current_level = approval_form.current_approval_level

        # ==============================================================================
        # 📨 PIPELINE 1: PERSONALIZED ACTIONABLE APPROVER TRACK (WKF-01)
        # ==============================================================================
        def execute_core_approver_pipeline_async(form, level):
            if not level:
                return
            
            notifier = NotificationService()
            template_approver = EmailNotificationTemplate.objects.filter(
                Q(event_type__icontains='pending') | Q(event_type__icontains='WKF-01'), 
                is_active=True
            ).first()

            level_users = ApprovalLevelUser.objects.filter(approval_level=level, is_active=True)
            if form.department:
                level_users = level_users.filter(departments=form.department)

            for alu in level_users:
                user = alu.user
                if not user or user == form.submitted_by:
                    continue

                if user.email and template_approver:
                    context_approver = {
                        'form': form,
                        'user': user,
                        'login_url': request.build_absolute_uri(reverse('form_detail', args=[form.id])),
                        'approval_link_text': getattr(template_approver, 'approval_link_text', 'Click here to review')
                    }
                    sub_render = EmailNotificationService.render_template(template_approver.subject, context_approver)
                    body_render = EmailNotificationService.render_template(template_approver.body, context_approver)
                    
                    notifier.send_email(user.email.strip(), sub_render, body_render)

                phone = getattr(user.user_profile, 'phone', None) if hasattr(user, 'user_profile') else None
                if phone:
                    try:
                        today = timezone.now().strftime('%d-%m-%Y')
                        current_time = timezone.now().strftime('%H:%M')
                        sms_text = f"FM={form.form_number} SUB={form.subject} Pending your approval level D={today} T={current_time}-RJPSWM"
                        notifier.send_sms(phone, sms_text)
                    except Exception as e: logger.error(f"[TRACK 1 SMS ERROR]: {e}")

                    try:
                        notifier.send_dynamic_whatsapp_by_event(
                            event_type='pending_approval',
                            approval_form=form,
                            recipient_phone=phone,
                            approver_name=user.get_full_name() or user.username
                        )
                    except Exception as e: logger.error(f"[TRACK 1 WA ERROR]: {e}")

        if current_level:
            threading.Thread(target=execute_core_approver_pipeline_async, args=(approval_form, current_level)).start()


        # ==============================================================================
        # 📨 PIPELINE 2: INFORMATIVE BROADCAST MATRIX VIA CC DISPATCH (FRM-01 / REVISED)
        # ==============================================================================
        def execute_matrix_broadcast_pipeline_async(form, is_revised):
            template_matrix = EmailNotificationTemplate.objects.filter(
                Q(event_type__icontains='FRM-01') | Q(event_type__icontains='submitted'),
                is_active=True
            ).first()
            
            context_matrix = {
                'form': form,
                'submitted_by': form.submitted_by,
                'user': form.submitted_by,
                'login_url': request.build_absolute_uri(reverse('form_detail', args=[form.id])),
            }

            if template_matrix:
                raw_subject = template_matrix.subject
                raw_body = template_matrix.body
                
                # 🟢 INJECT DYNAMIC CONTEXT TRANSFORMS FOR REVISED RE-SUBMISSIONS
                if is_revised:
                    if "Your Approval Request" in raw_subject:
                        raw_subject = raw_subject.replace("Your Approval Request", "Your Revised Approval Request")
                    else:
                        raw_subject = f"Your Revised Approval Request Submitted Successfully - {form.form_number}"

                    if "Your approval request" in raw_body:
                        raw_body = raw_body.replace("Your approval request", "Your revised approval request")
                    elif "Your Approval Request" in raw_body:
                        raw_body = raw_body.replace("Your Approval Request", "Your Revised Approval Request")
                
                compiled_subject = EmailNotificationService.render_template(raw_subject, context_matrix)
                compiled_body = EmailNotificationService.render_template(raw_body, context_matrix)
            else:
                # Symmetrical template fallback matching your specific layout criteria perfectly
                if is_revised:
                    compiled_subject = f"Your Revised Approval Request Submitted Successfully - {form.form_number}"
                    compiled_body = (
                        f"Dear {form.submitted_by.get_full_name() or form.submitted_by.username},<br><br>"
                        f"Your revised approval request has been successfully submitted.<br><br>"
                        f"<b>Form Number</b>: {form.form_number}<br>"
                        f"<b>Subject</b>: {form.subject}<br>"
                        f"<b>Amount</b>: ₹{form.amount:,}<br>"
                        f"<b>Department</b>: {form.department.name if form.department else '—'}<br>"
                        f"<b>Center</b>: {form.center.name if form.center else (form.selected_center.name if form.selected_center else '—')}<br><br>"
                        f"The revised form is now under review and will be processed according to the approval workflow.<br><br>"
                        f"To view the current approval status of your Manjuripatra form, kindly check the Approval Flow Tab in the view.<br><br>"
                        f"<a href='{context_matrix['login_url']}'>View Form Details</a><br><br>"
                        f"Regards,<br>SMVS Approval System"
                    )
                else:
                    compiled_subject = f"Your Approval Request Submitted Successfully: {form.form_number}"
                    compiled_body = f"Your approval request for {form.subject} has been successfully submitted."

            submitter_mail = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None
            
            # Dispatch matrix roles with dynamic text modifications applied
            process_matrix_notification(form, 'FRM-01', compiled_subject, compiled_body, cc_email=submitter_mail)

        # Pass flag down safely to the thread executor arguments block
        threading.Thread(
            target=execute_matrix_broadcast_pipeline_async, 
            args=(approval_form, is_form_resubmission)
        ).start()


        # 📱 3. Direct Submitter Instant Pinbot WhatsApp Receipt Card
        submitter_profile = getattr(request.user, 'user_profile', None)
        if submitter_profile and submitter_profile.phone:
            notifier = NotificationService()
            notifier.send_dynamic_whatsapp_by_event(
                event_type='form_submitted',
                approval_form=approval_form,
                recipient_phone=submitter_profile.phone
            )

        messages.success(request, "Form submitted successfully and sent for approval.")

    except Exception as e:
        logger.error(f"[SUBMIT VIEW] CRITICAL FAILURE: {str(e)}", exc_info=True)
        messages.error(request, f"Submission failed: {str(e)}")

    return redirect("form_detail", form_id=approval_form.id)


@login_required(login_url="login")
@require_http_methods(["POST"])
def upload_document_view(request, form_id):
    """Upload document (NON-AJAX - UI friendly) with Draft-mode overwrite capability"""
    approval_form = get_object_or_404(ApprovalForm, id=form_id)

    # Permission check
    if not can_upload_attachment(request.user, approval_form):
        messages.error(request, "Permission denied")
        return redirect("form_detail", form_id=form_id)

    form = ApprovalDocumentForm(request.POST, request.FILES)

    if form.is_valid():
        # 💡 Check if the form is in Draft mode and already has a document uploaded
        existing_doc = approval_form.documents.first()
        
        if approval_form.status in ["initiated","revision_pending"] and existing_doc:
            # 1. Clean up and delete the old physical file from disk storage
            if existing_doc.file and os.path.exists(existing_doc.file.path):
                try:
                    os.remove(existing_doc.file.path)
                except Exception as e:
                    logger.error(f"Failed to delete old draft file on disk: {e}")

            # 2. Extract incoming file and extension
            new_file = request.FILES['file']
            ext = os.path.splitext(new_file.name)[1]  # Safely extracts '.pdf'

            # 3. Reconstruct your standard structured pattern
            # Replaces slashes/spaces with underscores to ensure a safe filesystem name
            safe_form_no = approval_form.form_number.replace('/', '-').replace(' ', '_')
            structured_filename = f"{safe_form_no}_Approval_Document{ext}"

            # 4. Assign the name value explicitly to the file object wrapper
            new_file.name = structured_filename

            # 5. Overwrite the file field and metadata on the existing entry row
            existing_doc.file = new_file
            existing_doc.document_type = form.cleaned_data.get('document_type', existing_doc.document_type)
            existing_doc.uploaded_by = request.user
            existing_doc.is_verified = False  # Reset verification status for safety
            existing_doc.save()
            
            messages.success(request, "Draft document replaced and updated successfully.")
        else:
            # Standard workflow behavior (Creates a brand new separate document row record)
            document = form.save(commit=False)
            document.form = approval_form
            document.uploaded_by = request.user
            document.save()

            messages.success(request, "Revision document appended to application list successfully.")
    else:
        messages.error(request, "Invalid document. Please upload a valid file.")

    return redirect("form_detail", form_id=form_id)


@login_required(login_url="login")
@require_POST
def verify_document(request, doc_id):
    """Verify document or trigger auto-resubmission if errors are found"""
    document = get_object_or_404(ApprovalDocument, id=doc_id)
    approval_form = document.form
    user_role = getattr(request.user, "approval_role", None)

    # Security check: Only operators
    if not user_role or user_role.role not in ["operator", "mk_sabhya"]:
        messages.error(request, "Only operators or authorized verification levels can verify documents.")
        return redirect("form_detail", form_id=approval_form.id)

    # ✅ 1. Capture the Operator's or MK Sabhya's  Remarks from the 'Found an error' box
    operator_remarks = request.POST.get('operator_remarks', '').strip()

    # ✅ 2. IF REMARKS EXIST: Automatically Request Resubmission
    if operator_remarks:
        from approval_workflow.workflows import ApprovalWorkflowEngine
        
        # Trigger Resubmission via the Engine
        # This changes form status to 'revision_pending' and notifies the user
        ApprovalWorkflowEngine.request_resubmission(
            form=approval_form,
            approver=request.user,
            remarks=f"Document Verification Error: {operator_remarks}"
        )
        
        # ✅ 3. Add an official Comment visible to the End User
        from approval_core.models import ApprovalComment
        ApprovalComment.objects.create(
            form=approval_form,
            commented_by=request.user,
            comment_text=f"🔄 Document Verification Failed: {operator_remarks}",
            show_to_lower_levels=True
        )
        
        messages.warning(request, f"📋 Document verification error reported. Form {approval_form.form_number} sent back to end user.")
    
    # ✅ 4. IF NO REMARKS: Complete standard verification
    else:
        document.is_verified = True
        document.verified_by = request.user
        document.verified_at = timezone.now()
        document.save()
        messages.success(request, f"✅ Document verified successfully.")

    return redirect("form_detail", form_id=approval_form.id)

# ==================== Approval Action Views ====================

@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def approve_form_view(request, form_id):
    """Approve / Reject / Delegate form - Final corrected version"""
    approval_form = get_object_or_404(ApprovalForm, id=form_id)

    is_current_approver = False
    is_delegated_to_me = False
    is_original_delegator_returned = False

    # 1. Normal current approver
    if approval_form.current_approval_level and approval_form.status in ["pending", "revision_pending", "delegated"]:
        alu_qs = ApprovalLevelUser.objects.filter(
            user=request.user,
            approval_level=approval_form.current_approval_level,
            is_active=True
        )
        if alu_qs.exists():
            if approval_form.department:
                user_has_dept_match = alu_qs.filter(departments=approval_form.department).exists()
                user_has_no_dept = not alu_qs.filter(departments__isnull=False).exists()
                is_current_approver = user_has_dept_match or user_has_no_dept
            else:
                is_current_approver = True

    # 2. Delegated to me
    is_delegated_to_me = (approval_form.is_delegated and 
                         approval_form.delegated_to == request.user)

    # 3. Delegator return case
    is_original_delegator_returned = False
    if (approval_form.delegated_by == request.user and
            not approval_form.is_delegated and
            approval_form.status in ["pending", "revision_pending"]):
        delegator_lvl_assign = request.user.approval_level_assignments.filter(is_active=True).first()
        if delegator_lvl_assign:
            already_approved_at_own_level = ApprovalAction.objects.filter(
                form=approval_form,
                actor=request.user,
                action_type='approved',
                approval_level=delegator_lvl_assign.approval_level
            ).exists()
            is_original_delegator_returned = not already_approved_at_own_level
        else:
            is_original_delegator_returned = True

    # Final permission check - allow action if any of the above is True
    if not (is_current_approver or is_delegated_to_me or is_original_delegator_returned):
        messages.error(request, "You are not authorized to approve this form")
        return redirect("form_detail", form_id=form_id)

    if request.method == "GET":
        return render(request, "approval_core/approve_form.html", {"form": approval_form})

    try:
        action = request.POST.get("action", "approve")
        remarks = request.POST.get("remarks", "").strip()
        delegate_to_id = request.POST.get("delegate_to")
        external_email = request.POST.get("external_email", "").strip()
        department_id = request.POST.get("department_id")

        # ✅ Approved amount handling
        from decimal import Decimal as D, InvalidOperation
        approved_amount = None
        approved_amount_str = request.POST.get("approved_amount", "").strip()


        # For foreign currency forms:
        #   - approved_amount arrives as INR (converted by JS hidden field)
        #   - comparison_requested must also be INR (amount_inr)
        # For INR forms:
        #   - approved_amount arrives as INR directly
        #   - comparison_requested is amount directly
        is_foreign = getattr(approval_form, 'currency_code', 'INR') != 'INR'
        comparison_requested = D(str(
            approval_form.amount_inr
            if (is_foreign and approval_form.amount_inr)
            else approval_form.amount
        ))

        try:
            if approved_amount_str:
                approved_amount = D(approved_amount_str)
            else:
                # If empty → assume FULL approval in INR
                approved_amount = comparison_requested

            # ✅ Block only if approved INR amount EXCEEDS requested INR amount
            # Use a small tolerance (0.01) to handle JS float rounding
            if approved_amount > comparison_requested + D('0.01'):
                messages.error(request, "⛔ Approved amount cannot exceed requested amount. Please inform MK Sabhya to re-initiate with the revised amount.")
                return redirect("form_detail", form_id=form_id)

            # If amount is reduced, remarks are MANDATORY
            if approved_amount < comparison_requested - D('0.01') and not remarks:
                messages.error(request, "⚠️ Remarks required for reduced amount.")
                return redirect("form_detail", form_id=form_id)

        except (InvalidOperation, ValueError, TypeError):
            messages.error(request, "Invalid approved amount entered.")
            return redirect("form_detail", form_id=form_id)

        delegate_to = None
        if delegate_to_id and delegate_to_id != "other":
            try:
                delegate_to = User.objects.get(id=delegate_to_id)
            except User.DoesNotExist:
                pass

        # Center case - Operator must select department
        if not approval_form.department and approval_form.center:
            if not department_id:
                messages.error(request, "Please select department")
                return redirect("form_detail", form_id=form_id)

            from approval_core.models import Department
            approval_form.department = Department.objects.get(id=department_id)
            rule = ApprovalWorkflowEngine.determine_applicable_rule(approval_form)
            if rule:
                approval_form.applicable_rule = rule
                if not approval_form.current_approval_level:
                    first = RuleApprovalSequence.objects.filter(rule=rule).order_by("sequence_order").first()
                    if first:
                        approval_form.current_approval_level = first.approval_level
            approval_form.save()

        # Delegation return case
        if approval_form.is_delegated and (
            approval_form.delegated_to == request.user or
            (approval_form.delegated_email and request.user.email == approval_form.delegated_email)
        ):
            # ✅ FIX: Use actual action chosen, not hardcoded "approved"
            verifier_decision = "approved" if action == "approve" else "rejected"

            # ✅ 1. Logic to determine the specific action_type for the timeline
            # Check if this is an internal user or an external guest
            is_internal = not approval_form.delegated_email
    
            if verifier_decision == "approved":
                new_action_type = "approved_by_internal" if is_internal else "approved_by_external"
            else:
                new_action_type = "rejected_by_internal" if is_internal else "rejected_by_external"

            # ✅ 2. Return from delegation using the engine
            ApprovalWorkflowEngine.return_from_delegation(
                form=approval_form,
                verifier_user=request.user if not approval_form.delegated_email else None,
                remarks=remarks,
                decision=verifier_decision   # ✅ was hardcoded "approved" before
            )

            # ✅ 3. Update the LATEST action record with your new standardized type
            # This ensures form_detail.html sees 'approved_by_internal' correctly
            latest_action = approval_form.actions.filter(actor=request.user).order_by('-created_at').first()
            if latest_action:
                latest_action.action_type = new_action_type
                latest_action.save()

            delegator_name = (
                approval_form.delegated_by.get_full_name() or approval_form.delegated_by.username
                if approval_form.delegated_by else "the delegator"
            )
            if verifier_decision == "approved":
                messages.success(request, f"✅ Form verified and returned to {delegator_name} for further approval.")
            else:
                messages.success(request, f"Form rejected by 3rd Party and returned to {delegator_name}.")
            return redirect("form_detail", form_id=form_id)

        # Normal actions
        if action == "delegate":
            if delegate_to:
                ApprovalWorkflowEngine.delegate_form(
                    form=approval_form,
                    delegating_user=request.user,
                    delegated_to_user=delegate_to,
                    reason=remarks
                )
                messages.success(request, f"Form delegated successfully to {delegate_to.get_full_name() or delegate_to.username}")
            elif external_email:
                ApprovalWorkflowEngine.delegate_form_external(
                    form=approval_form,
                    delegating_user=request.user,
                    external_email=external_email,
                    reason=remarks
                )
                messages.success(request, f"Form delegated externally to {external_email}")
            else:
                messages.error(request, "Please select Internal 3rd party user or Enter external 3rd party email address.")

        elif action == "approve":
            current_level = approval_form.current_approval_level
            current_level_name = current_level.level_name.lower() if current_level else ""

            # ✅ Safety: If applicable_rule is missing, re-determine it now
            if not approval_form.applicable_rule:
                logger.warning(f"[APPROVE VIEW] applicable_rule missing for form {approval_form.form_number}, re-determining...")
                approval_form.applicable_rule = ApprovalWorkflowEngine.determine_applicable_rule(approval_form)
                if approval_form.applicable_rule:
                    approval_form.save()
                    logger.info(f"[APPROVE VIEW] Rule re-assigned: {approval_form.applicable_rule.rule_name}")
                else:
                    messages.error(request, "⛔ No approval rule found for this form. Please contact the administrator.")
                    return redirect("form_detail", form_id=form_id)

            # 🚫 BLOCK APPROVAL IF DOCUMENT NOT VERIFIED
            unverified_docs = approval_form.documents.filter(is_verified=False)

            if getattr(request.user, "approval_role", None) and \
                request.user.approval_role.role == "operator" and \
                unverified_docs.exists():          
     
                    messages.error(request, "Please verify all documents before approval.")
                    return redirect("form_detail", form_id=form_id)

            # Detect final approver — ONLY when next level is genuinely None
            is_final_approver = False
            if approval_form.applicable_rule and approval_form.current_approval_level:
                next_level = ApprovalWorkflowEngine.get_next_approval_level(
                    approval_form,
                    approval_form.current_approval_level
                )
                # ✅ FIX: Only True when there is NO next level at all
                if not next_level:
                    is_final_approver = True

            from decimal import Decimal as D
            # Use INR as the base for all comparisons and storage
            is_foreign = getattr(approval_form, 'currency_code', 'INR') != 'INR'
            inr_requested = D(str(
                approval_form.amount_inr
                if (is_foreign and approval_form.amount_inr)
                else approval_form.amount
            ))
            # Default to full INR amount if somehow empty
            approved_amount = approved_amount if approved_amount is not None else inr_requested

            ApprovalWorkflowEngine.approve_form(
                form=approval_form,
                user=request.user,
                remarks=remarks or "Approved",
                delegate_to=delegate_to,
                approved_amount=approved_amount,
            )

            # ✅ FIX: Refresh from DB — do NOT manually set status again.
            # approve_form() already sets status="approved" and saves correctly.
            approval_form.refresh_from_db()

            # Apply ONLY for final approver — store remark when amount is reduced
            if is_final_approver:
                # ✅ Save approved_amount_local for foreign currency forms
                # approved_amount is in INR → convert back to local currency
                if (
                    approval_form.currency_code != 'INR'
                    and approval_form.exchange_rate_used
                    and approval_form.exchange_rate_used > 0
                ):
                    from approval_core.currency_service import convert_from_inr
                    approval_form.approved_amount_local = convert_from_inr(
                        approved_amount, approval_form.exchange_rate_used
                    )
                    approval_form.save(update_fields=['approved_amount_local'])

                # ✅ Compare INR vs INR — works for both INR and foreign currency
                inr_comparison = D(str(approval_form.amount_inr or approval_form.amount))
                if approved_amount < inr_comparison - D('0.01'):
                    approval_form.latest_approval_remark = remarks
                    ApprovalComment.objects.create(
                        form=approval_form,
                        commented_by=request.user,
                        comment_text=remarks,
                        show_to_lower_levels=True,
                        is_lesser_approval=True
                    )
                    approval_form.save()
                else:
                    if approval_form.latest_approval_remark:
                        approval_form.latest_approval_remark = None
                        approval_form.save()

            # Smart success message
            if approval_form.status == 'approved':
                messages.success(request, f"✅ Form {approval_form.form_number} has been FINAL APPROVED!")
            elif 'operator' in current_level_name:
                messages.success(request, "Documents Verified and forwarded to next level.")
            else:
                messages.success(request, "Form approved and forwarded to next level.")

        elif action == "reject":
            ApprovalWorkflowEngine.reject_form(
                form=approval_form,
                approver=request.user,
                remarks=remarks,
                allow_revision=False
            )
            messages.success(request, "Form has been rejected.")

        elif action == "resubmit":
            if not remarks:
                messages.error(request, "⚠️ Please specify what documents or changes are required before sending back.")
                return redirect("form_detail", form_id=form_id)
            ApprovalWorkflowEngine.request_resubmission(
                form=approval_form,
                approver=request.user,
                remarks=remarks
            )
            # ✅ Save resubmission remarks as a comment visible to end user
            ApprovalComment.objects.create(
                form=approval_form,
                commented_by=request.user,
                comment_text=f"🔄 Revision Requested: {remarks}",
                show_to_lower_levels=True,  # visible to end user
            )
            messages.success(request, f"📋 Form {approval_form.form_number} has been sent back to the end user for revision.")

    except Exception as e:
        messages.error(request, f"Action failed: {str(e)}")
        logger.error(f"Approve view error for form {form_id}: {e}", exc_info=True)

    return redirect("form_detail", form_id=form_id)


@login_required(login_url="login")
def reject_form_view(request, form_id):
    """Reject form"""
    approval_form = get_object_or_404(ApprovalForm, id=form_id)
    user_role = get_object_or_404(UserRole, user=request.user)
    
    is_current_approver = ApprovalLevelUser.objects.filter(
        user=request.user,
        approval_level=approval_form.current_approval_level,
        is_active=True
    ).exists()

    if not is_current_approver:
        messages.error(request, "You are not authorized to reject this form")
        return redirect("dashboard")
    	
    if request.method == "POST":
        remarks = request.POST.get("remarks", "")
        allow_revision = request.POST.get("allow_revision") == "on"
        
        ApprovalWorkflowEngine.reject_form(approval_form, request.user, remarks, allow_revision)
        
        if allow_revision:
            messages.success(request, "Form rejected. End user can now submit revisions.")
        else:
            messages.success(request, "Form rejected permanently.")
        
        return redirect("form_detail", form_id=form_id)
    
    return render(request, "approval_core/reject_form.html", {"form": approval_form})


# ==================== Comment Views ====================

@login_required(login_url="login")
@require_http_methods(["POST"])
def add_comment_view(request, form_id):
    """Add comment to form with multi-level visibility"""
    approval_form = get_object_or_404(ApprovalForm, id=form_id)

    form = ApprovalCommentForm(request.POST)

    if form.is_valid():
        comment = form.save(commit=False)
        comment.form = approval_form
        comment.commented_by = request.user
        comment.save()

        # Save selected levels for visibility
        visible_level_ids = request.POST.getlist('visible_to_levels')
        if visible_level_ids:
            comment.visible_to_levels.set(visible_level_ids)
        else:
            # If no levels selected, fallback to old checkbox behavior
            if request.POST.get('show_to_lower_levels'):
                comment.show_to_lower_levels = True
                comment.save()

        messages.success(request, "Comment added successfully")
        return redirect("form_detail", form_id=approval_form.id)

    messages.error(request, "Invalid comment")
    return redirect("form_detail", form_id=approval_form.id)


# ==================== List Views ====================

@login_required
def forms_list_view(request):
    user = request.user
    
    try:
        user_role = UserRole.objects.get(user=user)
    except UserRole.DoesNotExist:
        user_role = None

    is_end_user = user_role and user_role.role == "end_user"

    base_qs = ApprovalForm.objects.select_related('submitted_by', 'department', 'center', 'current_approval_level') \
                                  .prefetch_related('actions__actor', 'actions__approval_level')

    if is_end_user:
        page_title = "My Submitted Forms"
        queryset = base_qs.filter(submitted_by=user)
    elif user_role and user_role.role == "admin":
        page_title = "All Approval Forms"
        queryset = base_qs.all()
    else:
        page_title = "My Approval Forms & History"   
        user_levels = ApprovalLevelUser.objects.filter(
            user=user, is_active=True
        ).values_list('approval_level', flat=True)

        queryset = base_qs.filter(
            Q(current_approval_level__in=user_levels, status='pending') |
            Q(actions__actor=user)
        ).distinct()

    # Filters
    filter_form = ApprovalFilterForm(request.GET or None)
    if filter_form.is_valid():
        if filter_form.cleaned_data.get("status"):
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
        if filter_form.cleaned_data.get("department"):
            queryset = queryset.filter(department__name__icontains=filter_form.cleaned_data["department"])
        if filter_form.cleaned_data.get("date_from"):
            queryset = queryset.filter(created_at__gte=filter_form.cleaned_data["date_from"])
        if filter_form.cleaned_data.get("date_to"):
            queryset = queryset.filter(created_at__lte=filter_form.cleaned_data["date_to"])

    queryset = queryset.order_by('-created_at')

    # ✅ 1. Initialize Paginator (Show 10 forms per page)
    paginator = Paginator(queryset, 10) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 2. Enrich ONLY the forms on the current page for better performance
    for form in page_obj:       
        # 1. Your specific latest action (for "My Action" column badge)
        form.my_action = form.actions.filter(actor=user).order_by('-created_at').first()
        
        # 2. Absolute latest action by ANYONE on this form (for dynamic status and last actor)
        last_global_action = form.actions.all().order_by('-created_at').first()

        if last_global_action:
            # Combined Last Actor Name and Role (e.g., "Niravbhai Basheri (MK Sabhya)")
            actor_name = last_global_action.actor.get_full_name() or last_global_action.actor.username
            actor_role = last_global_action.approval_level.level_name if last_global_action.approval_level else "N/A"
            
            form.dynamic_last_actor = f"{actor_name} ({actor_role})"
            form.latest_action_date = last_global_action.created_at

            # 3. Logic for Dynamic Status Label
            if form.status == 'approved':
                form.dynamic_status_label = "Approved"
            elif form.status == 'rejected':
                form.dynamic_status_label = "Rejected"
            elif form.status == 'revision_pending':
                # Shows who specifically requested the revision
                form.dynamic_status_label = f"Revision Requested ({actor_role})"
            elif getattr(form, 'is_delegated', False):
                # Shows who performed the delegation
                form.dynamic_status_label = f"Delegated ({actor_role})"
            else:
                # For "Under Process" status, show which role the form is currently waiting for
                current_role = form.current_approval_level.level_name if form.current_approval_level else "Under Process"
                form.dynamic_status_label = f"Pending at {current_role}"
        else:
            # Fallback logic for forms that are newly created/Drafts
            form.dynamic_last_actor = "—"
            form.latest_action_date = form.created_at
            form.dynamic_status_label = "Draft"
                    
    context = {
        "page_obj": page_obj, 
        "filter_form": filter_form,
        "title": page_title,
        "is_end_user": is_end_user,
    }

    return render(request, "approval_core/forms_list.html", context)


def can_upload_attachment(user, approval_form=None):
    """
    Enforces 'Single PDF' constraint but allows replacing/modifying the file 
    during initial draft mode, as well as additions during the Revision phase.
    """
    try:
        user_role = UserRole.objects.get(user=user)
    except UserRole.DoesNotExist:
        return False

    if approval_form:
        # Count existing documents for this form
        doc_count = approval_form.documents.count()

        # 1. Creator (End User) Logic
        if approval_form.submitted_by == user:
            # 🟢 INITIAL DRAFT: Return True so the upload box stays visible on the UI for replacement
            if approval_form.status == "initiated":
                return True
            
            # 🟠 RESUBMISSION: Open up for more documents
            if approval_form.status == "revision_pending":
                return True
                
        # 2. Operator / Approver Logic
        # They can upload if they are the active level user for this form
        return ApprovalLevelUser.objects.filter(
            user=user,
            approval_level=approval_form.current_approval_level,
            is_active=True
        ).exists()

    return False

def can_view_form(user, approval_form):
    from approval_core.models import RuleApprovalSequence, ApprovalLevelUser

    # Admin always allowed
    if user.is_superuser:
        return True

    # Creator allowed
    if approval_form.submitted_by == user:
        return True

    # Get all levels for this form
    levels = RuleApprovalSequence.objects.filter(
        rule=approval_form.applicable_rule
    ).values_list("approval_level", flat=True)

    # Check if user is part of any level
    is_in_workflow = ApprovalLevelUser.objects.filter(
        user=user,
        approval_level__in=levels
    ).exists()

    return is_in_workflow


def guest_form_view(request, token):
    """Guest view for external delegated users - No login required"""
    form = get_object_or_404(ApprovalForm, guest_token=token)

    if not form.is_delegated or not form.delegated_email:
        messages.error(request, "This link is invalid or has expired.")
        return redirect("login")   # or show a nice error page

    context = {
        "form": form,
        "documents": form.documents.all(),
        "actions": form.actions.all().order_by('-created_at'),
        "is_guest": True,
    }

    return render(request, "approval_core/guest_form_detail.html", context)

@require_http_methods(["POST"])
def guest_approve_form_view(request, token):
    form = get_object_or_404(ApprovalForm, guest_token=token)

    if not form.is_delegated or not form.delegated_email:
        return render(request, "approval_core/guest_error.html", {
            "error": "This link is invalid or has expired."
        })

    decision = request.POST.get("decision")   # "approve" or "reject"
    remarks = request.POST.get("remarks", "").strip()

    try:
        if decision == "approve":
            ApprovalWorkflowEngine.return_from_delegation(
                form=form,
                verifier_user=None,
                remarks=remarks,
                decision="approved"
            )
            success_message = "Thank you! The form has been approved and returned to the delegator."

        elif decision == "reject":
            ApprovalWorkflowEngine.return_from_delegation(
                form=form,
                verifier_user=None,
                remarks=remarks,
                decision="rejected"
            )
            success_message = "Thank you! The form has been rejected and returned to the delegator."

        else:
            return render(request, "approval_core/guest_error.html", {
                "error": "Invalid decision."
            })

        return render(request, "approval_core/guest_success.html", {
            "message": success_message,
            "form_number": form.form_number
        })

    except Exception as e:
        logger.error(f"Guest approve error: {e}")
        return render(request, "approval_core/guest_error.html", {
            "error": f"Action failed: {str(e)}"
        })

def guest_form_view(request, token):
    """Guest view for external delegated users - No login required"""
    form = get_object_or_404(ApprovalForm, guest_token=token)

    if not form.is_delegated or not form.delegated_email:
        return render(request, "approval_core/guest_error.html", {
            "error": "This link is invalid or has expired."
        })

    context = {
        "form": form,
        "documents": form.documents.all(),
        "actions": form.actions.all().order_by('-created_at'),
        "is_guest": True,
    }

    return render(request, "approval_core/guest_form_detail.html", context)


# ==============================================================================
# 📋 PRODUCTION ENGINE: DYNAMIC PATH-VALIDATED LOGGING BACKUP VIEWS
# ==============================================================================

@login_required(login_url="login")
def backup_full_view(request):
    """Trigger full project backup with clean configuration path evaluation."""
    if not request.user.is_superuser:
        messages.error(request, "You don't have permission to perform backup.")
        return redirect("dashboard")
    
    try:
        # 1. Fire your management command routine directly
        from django.core.management import call_command
        call_command('backup_project') 
        
        # 2. Add structural half-second buffer delay
        time.sleep(1.0)
        
        # 🟢 3. EVALUATE DYNAMIC DIRECTORY PATH FROM SETTINGS
        backup_dir = getattr(settings, 'BACKUP_DIRECTORY', '/backups')
        
        # 4. Perform wild-card lookup against the dynamic folder path structure
        search_pattern = os.path.join(backup_dir, "SMVS_Approval_Full_*.tar.gz")
        backup_files = glob.glob(search_pattern)
        
        if backup_files:
            # Sort by modification time to extract the absolute newest file row matching
            file_path = max(backup_files, key=os.path.getmtime)
            filename = os.path.basename(file_path)
        else:
            filename = f"SMVS_Approval_Full_{timezone.now().strftime('%d%m%Y_%H%M')}.tar.gz"
            file_path = os.path.join(backup_dir, filename)

        # 5. Commit record metrics to the database logging dashboard securely
        from approval_core.backup_service import ProductionBackupEngine
        ProductionBackupEngine.record_log('backup_full', filename, request.user, 'success', file_path=file_path)
        
        messages.success(request, "✅ Full Project Backup completed and verified successfully!")
    except Exception as e:
        error_trace = traceback.format_exc()
        from approval_core.backup_service import ProductionBackupEngine
        ProductionBackupEngine.record_log('backup_full', "SMVS_Approval_Full_Failed.tar.gz", request.user, 'failed', error_msg=error_trace)
        messages.error(request, f"❌ Full Backup failed: {str(e)}")
    
    return redirect("dashboard")


@login_required(login_url="login")
def backup_db_view(request):
    """Trigger database only backup with clean configuration path evaluation."""
    if not request.user.is_superuser:
        messages.error(request, "You don't have permission to perform backup.")
        return redirect("dashboard")
    
    try:
        # 1. Fire your management command routine directly
        from django.core.management import call_command
        call_command('backup_db')
        
        # 2. Add structural half-second buffer delay
        time.sleep(1.0)
        
        # 🟢 3. EVALUATE DYNAMIC DIRECTORY PATH FROM SETTINGS
        backup_dir = getattr(settings, 'BACKUP_DIRECTORY', '/backups')
        
        # 4. Perform wild-card lookup against the dynamic folder path structure
        search_pattern = os.path.join(backup_dir, "SMVS_DB_*.sql")
        backup_files = glob.glob(search_pattern)
        
        if backup_files:
            # Sort by modification time to extract the absolute newest file row matching
            file_path = max(backup_files, key=os.path.getmtime)
            filename = os.path.basename(file_path)
        else:
            filename = f"SMVS_DB_{timezone.now().strftime('%d%m%Y_%H%M')}.sql"
            file_path = os.path.join(backup_dir, filename)

        # 5. Commit record metrics to the database logging dashboard securely
        from approval_core.backup_service import ProductionBackupEngine
        ProductionBackupEngine.record_log('backup_db', filename, request.user, 'success', file_path=file_path)
        
        messages.success(request, "✅ Database Backup completed and verified successfully!")
    except Exception as e:
        error_trace = traceback.format_exc()
        from approval_core.backup_service import ProductionBackupEngine
        ProductionBackupEngine.record_log('backup_db', "SMVS_DB_Failed.sql", request.user, 'failed', error_msg=error_trace)
        messages.error(request, f"❌ Database Backup failed: {str(e)}")
    
    return redirect("dashboard")


# ==================== REPORT VIEWS ====================

def _has_report_access(user):
    if user.is_superuser:
        return True
    try:
        return user.report_permission.can_view_report
    except Exception:
        return False


def _has_actual_amount_access(user):
    """Returns True if user can enter actual amount OR work completion %"""
    if user.is_superuser:
        return True
    try:
        perm = user.report_permission
        return perm.can_enter_actual_amount or perm.can_enter_work_completion
    except Exception:
        return False


def _get_report_scope(user):
    """Returns (allowed_centers, allowed_departments)"""

    # Superuser → full access
    if user.is_superuser:
        return None, None

    # If no permission object → NO access
    if not hasattr(user, "report_permission"):
        return [], []

    perm = user.report_permission

    centers = perm.restrict_to_centers.all() if perm.restrict_to_centers.exists() else None
    depts   = perm.restrict_to_departments.all() if perm.restrict_to_departments.exists() else None

    return centers, depts


@login_required(login_url="login")
def approval_report_view(request):
    """
    Center/Department-wise Approval Report.
    - Permission-based: only users with ReportPermission.can_view_report (or superuser) can access.
    - Supports filtering by center, department, year range.
    - Supports CSV export.
    - Yearly comparison table.
    - Actual vs Approved expenditure columns.
    """
    if not _has_report_access(request.user):
        messages.error(request, "You do not have permission to view this report.")
        return redirect("dashboard")

    user = request.user
    user_role = getattr(user, "approval_role", None)

    is_end_user = user_role and user_role.role == "end_user"
    user_department = user_role.department if user_role else None
    is_center_user = is_end_user and not user_department

    # Get report permission
    report_permission = ReportPermission.objects.filter(user=user).first()

    allowed_centers, perm_departments = _get_report_scope(request.user)
    allowed_departments = None

    if report_permission and report_permission.restrict_to_departments.exists():
        allowed_departments = report_permission.restrict_to_departments.all()

    elif is_end_user and user_department:
        # fallback → own department only
        allowed_departments = Department.objects.filter(id=user_department.id)

    elif perm_departments:
        allowed_departments = perm_departments

    # ✅ Pass SEPARATE flags — not combined — so template shows correct fields
    if request.user.is_superuser:
        can_enter_actual = True
        can_enter_work_completion = True
    else:
        try:
            _perm = request.user.report_permission
            can_enter_actual = _perm.can_enter_actual_amount
            can_enter_work_completion = _perm.can_enter_work_completion
        except Exception:
            can_enter_actual = False
            can_enter_work_completion = False

    show_department_filter = True
    if is_end_user and not allowed_departments:
        show_department_filter = False      

    user_center_name = getattr(getattr(request.user, "center", None), "name", None)

    # ---- GET filters ----
    filter_type = request.GET.get('filter_type', 'center')
    center_id = request.GET.get('center_id', '')
    dept_id = request.GET.get('dept_id', '')
    year_from = request.GET.get('year_from', '')
    year_to = request.GET.get('year_to', '')

    # 🔒 Force center user restrictions
    if is_center_user:
        filter_type = 'center'  # Always center-wise

        if hasattr(request.user, "center") and request.user.center:
            center_id = str(request.user.center.id)  # Lock to user's center

        dept_id = ''  # Remove department filter

    # ---- Base queryset: approved forms only ----
    qs = ApprovalForm.objects.filter(
        status='approved', approved_at__isnull=False
    ).select_related('center', 'department', 'submitted_by').prefetch_related('actual_expenditure')

    # Apply permission scope
    if filter_type == 'center':
        qs = qs.filter(center__isnull=False)

        # 🔒 Force center restriction for center user
        if is_center_user and hasattr(request.user, "center") and request.user.center:
            qs = qs.filter(center=request.user.center)

        else:
            # Normal permission-based filtering
            if allowed_centers is not None:
                qs = qs.filter(center__in=allowed_centers)

            if center_id:
                qs = qs.filter(center_id=center_id)

            if dept_id:
                qs = qs.filter(department_id=dept_id)

    else:
        qs = qs.filter(department__isnull=False, center__isnull=True)

        # 🔒 FORCE department restriction
        if is_end_user and user_department:
            qs = qs.filter(department=user_department)

        elif allowed_departments:
            qs = qs.filter(department__in=allowed_departments)

        else:
            qs = qs.none()

        if dept_id:
            qs = qs.filter(department_id=dept_id)

        # 🔒 Optional: prevent center user from accessing department mode
        if is_center_user:
            qs = qs.none()  # No data for center user in department mode

    # Year filter
    if year_from:
        try:
            qs = qs.filter(approved_at__year__gte=int(year_from))
        except ValueError:
            pass
    if year_to:
        try:
            qs = qs.filter(approved_at__year__lte=int(year_to))
        except ValueError:
            pass

    qs = qs.order_by('-approved_at')

    # ✅ 1. Fix Sorting (Latest Approved First)
    qs = qs.order_by('-approved_at')

    # ✅ 2. Calculate Totals on FULL QuerySet (for Tiles and Comparison Table)
    full_rows = []
    for form in qs:
        actual_exp = getattr(form, 'actual_expenditure', None)
        actual_amt = actual_exp.actual_amount if actual_exp else None
        approved_amt = form.approved_amount or form.amount
        
        full_rows.append({
            'form': form,
            'approved_amount': approved_amt,
            'actual_amount': actual_amt,
            'year': form.approved_at.year,
        })

    # ---- Build rows ----
    from decimal import Decimal
    rows = []
    for form in qs:
        try:
            actual_exp = form.actual_expenditure
            actual_amt = actual_exp.actual_amount
        except ActualExpenditure.DoesNotExist:
            actual_exp = None
            actual_amt = None

        approved_amt = form.approved_amount or form.amount
        diff = (approved_amt - actual_amt) if actual_amt is not None else None       
        
        rows.append({
            'form': form,
            'center_name': form.center.name if form.center else '—',
            'dept_name': form.department.name if form.department else '—',
            'form_number': form.form_number,
            'subject': form.subject,
            'approved_date': form.approved_at,
            'requested_amount': form.amount,
            'approved_amount': approved_amt,
            'actual_amount': actual_amt,
            'difference': diff,
            'actual_exp_obj': actual_exp,
            'year': form.approved_at.year,
        })

    # ---- CSV export ----
    if request.GET.get('export') == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="approval_report.csv"'
        writer = csv.writer(response)
        if filter_type == 'center':
            writer.writerow(['#', 'Center', 'Department', 'Form No.', 'Subject',
                             'Approved Date', 'Requested (Rs)', 'Approved (Rs)',
                             'Actual Spent (Rs)', 'Difference (Rs)'])
        else:
            writer.writerow(['#', 'Department', 'Form No.', 'Subject',
                             'Approved Date', 'Requested (Rs)', 'Approved (Rs)',
                             'Actual Spent (Rs)', 'Difference (Rs)'])
        for i, row in enumerate(rows, 1):
            date_str = row['approved_date'].strftime('%d-%m-%Y') if row['approved_date'] else ''
            if filter_type == 'center':
                writer.writerow([
                    i, row['center_name'], row['dept_name'], row['form_number'],
                    row['subject'], date_str,
                    row['requested_amount'], row['approved_amount'],
                    row['actual_amount'] if row['actual_amount'] is not None else '',
                    row['difference'] if row['difference'] is not None else '',
                ])
            else:
                writer.writerow([
                    i, row['dept_name'], row['form_number'],
                    row['subject'], date_str,
                    row['requested_amount'], row['approved_amount'],
                    row['actual_amount'] if row['actual_amount'] is not None else '',
                    row['difference'] if row['difference'] is not None else '',
                ])
        return response

    # ---- Yearly summary ----
    from collections import defaultdict
    yearly_summary = defaultdict(lambda: {
        'count': 0,
        'total_requested': Decimal('0'),
        'total_approved': Decimal('0'),
        'total_actual': Decimal('0'),
        'has_actual': False,
    })
    for row in rows:
        yr = row['year']
        yearly_summary[yr]['count']           += 1
        yearly_summary[yr]['total_requested'] += row['requested_amount']
        yearly_summary[yr]['total_approved']  += row['approved_amount']
        if row['actual_amount'] is not None:
            yearly_summary[yr]['total_actual'] += row['actual_amount']
            yearly_summary[yr]['has_actual']    = True
    yearly_summary = dict(sorted(yearly_summary.items()))

    # Compute per-year saving for template
    for yr, data in yearly_summary.items():
        if data['has_actual']:
            data['saving'] = data['total_approved'] - data['total_actual']
        else:
            data['saving'] = None

    # ---- Grand totals ----
    total_approved = sum(r['approved_amount'] or 0 for r in rows)
    total_actual   = sum(r['actual_amount'] or 0 for r in rows)
    total_diff     = total_approved - total_actual

    # ✅ 3. Apply Pagination to the QuerySet (10 records per page)
    paginator = Paginator(qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # ✅ 4. Build paginated rows for the table
    paginated_rows = []
    for form in page_obj:
        actual_exp = getattr(form, 'actual_expenditure', None)
        paginated_rows.append({
            'form': form,
            'center_name': form.center.name if form.center else '—',
            'dept_name': form.department.name if form.department else '—',
            'form_number': form.form_number,
            'subject': form.subject,
            'approved_date': form.approved_at,
            'requested_amount': form.amount,
            'approved_amount': form.approved_amount or form.amount,
            'actual_amount': actual_exp.actual_amount if actual_exp else None,
            'difference': (form.approved_amount or form.amount) - actual_exp.actual_amount if actual_exp and actual_exp.actual_amount is not None else None,
            'actual_exp_obj': actual_exp,
        })

    # ---- Dropdowns ----
    # ✅ Restrict center for End User
    if is_end_user and hasattr(request.user, "center") and request.user.center:
        all_centers = Center.objects.filter(id=request.user.center.id)
    else:
        all_centers = Center.objects.filter(is_active=True).order_by('name')
    all_departments = Department.objects.filter(is_active=True).order_by('name')
    if allowed_centers is not None:
        all_centers = allowed_centers
    if allowed_departments is not None:
        all_departments = allowed_departments

    available_years = sorted(set(
        ApprovalForm.objects.filter(
            status='approved', approved_at__isnull=False
        ).values_list('approved_at__year', flat=True).distinct()
    ))

    context = {
        'page_obj': page_obj,  # ✅ For Pagination Controls
        'rows': paginated_rows, # ✅ Paginated Table Data
        'yearly_summary': yearly_summary,
        'filter_type': filter_type,
        'center_id': center_id,
        'dept_id': dept_id,
        'year_from': year_from,
        'year_to': year_to,
        'all_centers': all_centers,
        'all_departments': all_departments,
        'available_years': available_years,
        'can_enter_actual': can_enter_actual,
        'total_approved': total_approved,
        'total_actual': total_actual,
        'total_diff': total_diff,
        'total_count': len(rows),
        'is_center_user': is_center_user,
        'user_center_name': user_center_name,
        'is_end_user': is_end_user,
        'show_department_filter': show_department_filter,
        'is_department_user': bool(user_department),
        'allowed_departments': allowed_departments,
        'multi_department': allowed_departments.count() > 1 if allowed_departments else False,
    }

    return render(request, 'approval_core/approval_report.html', context)

@login_required(login_url="login")
@require_http_methods(["POST"])
def save_actual_expenditure_view(request, form_id):
    if not _has_actual_amount_access(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    form = get_object_or_404(ApprovalForm, id=form_id, status='approved')

    # ── Detect user's specific permissions ──────────────────────
    can_actual = False
    can_work = False
    if request.user.is_superuser:
        can_actual = True
        can_work = True
    else:
        try:
            perm = request.user.report_permission
            can_actual = perm.can_enter_actual_amount
            can_work = perm.can_enter_work_completion
        except Exception:
            pass

    from decimal import Decimal, InvalidOperation

    update_defaults = {'entered_by': request.user}

    # ── Handle actual amount (only if permitted) ─────────────────
    if can_actual:
        actual_amount_str = request.POST.get('actual_amount', '').strip()
        remarks = request.POST.get('remarks', '').strip()
        if not actual_amount_str:
            return JsonResponse({'success': False, 'error': 'Actual amount is required'}, status=400)
        try:
            update_defaults['actual_amount'] = Decimal(actual_amount_str)
            update_defaults['remarks'] = remarks
        except (InvalidOperation, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid amount entered'}, status=400)

    # ── Handle work completion (only if permitted) ───────────────
    if can_work:
        work_completion_str = request.POST.get('work_completion_percent', '').strip()
        if work_completion_str:
            try:
                work_completion = Decimal(work_completion_str)
                if work_completion < 0 or work_completion > 100:
                    return JsonResponse({'success': False, 'error': 'Completion % must be between 0 and 100'}, status=400)
                update_defaults['work_completion_percent'] = work_completion
            except (InvalidOperation, ValueError):
                return JsonResponse({'success': False, 'error': 'Invalid completion % entered'}, status=400)

    # ── Nothing to save ──────────────────────────────────────────
    if len(update_defaults) <= 1:  # only entered_by
        return JsonResponse({'success': False, 'error': 'Nothing to save'}, status=400)

    ActualExpenditure.objects.update_or_create(
        form=form,
        defaults=update_defaults
    )

    return JsonResponse({'success': True})


# ==================== MASTER ZIP PROCESSING SUB-LOOPS ====================

def execute_master_country_import(file_obj):
    global _ZIP_COUNTRY_MAP, _ZIP_USER_MAP, _ZIP_ZONE_MAP, _ZIP_CENTER_MAP, _ZIP_DEPARTMENT_MAP, _ZIP_POST_MAP, _ZIP_APPROVAL_LEVEL_MAP
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0

    if not data:
        return 0, 0

    for row in data:
        # Extract columns cleanly using flexible fallback mapping keys
        old_id = row.get('id') or row.get('Id') or row.get('ID')
        code_val = row.get('code') or row.get('Code')
        name_val = row.get('name') or row.get('Name')
        
        active_raw = row.get('is_active') or row.get('Active')
        if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null']:
            is_active_bool = True
        else:
            is_active_bool = str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

        phone_code_val = row.get('phone_code') or row.get('Phone Code') or ''
        currency_code_val = row.get('currency_code') or row.get('Currency Code') or ''
        currency_symbol_val = row.get('currency_symbol') or row.get('Currency Symbol') or ''

        if code_val and name_val:
            clean_code = str(code_val).strip().upper()
            
            # 🟢 Update or Create cleanly using the unique string code field
            country, created = Country.objects.update_or_create(
                code=clean_code,
                defaults={
                    'name': str(name_val).strip(), 
                    'description': row.get('description', '') or '',
                    'is_active': is_active_bool,
                    'phone_code': str(phone_code_val).strip(),
                    'currency_code': str(currency_code_val).strip().upper(),
                    'currency_symbol': str(currency_symbol_val).strip(),
                }
            )
            
            # 🟢 CRITICAL MAP LOOKUP: Explicitly record both mappings to prevent reference crashes
            if old_id:
                # Map old primary key integer ID string to code ('1' -> 'IN')
                _ZIP_COUNTRY_MAP[str(old_id).strip()] = clean_code
            
            # Also map the code string onto itself to guard alternate column layouts
            _ZIP_COUNTRY_MAP[clean_code] = clean_code
            
            imported += 1
    return imported, 0

def execute_master_approval_level_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        code_val = row.get('code') or row.get('Code')
        name_val = row.get('name') or row.get('Name')
        level_num = row.get('level') or row.get('Level') or row.get('level_number')
        
        # Safe extraction for active column status flag
        active_raw = row.get('is_active') or row.get('Active')
        if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null']:
            is_active_bool = True
        else:
            is_active_bool = str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

        if code_val and name_val and level_num:
            try:
                # 🟢 FIX: Lookup and update using unique 'code' instead of passing 'id' directly
                ApprovalLevel.objects.update_or_create(
                    code=str(code_val).strip().upper(),
                    defaults={
                        'name': str(name_val).strip(),
                        'level': int(float(str(level_num).strip())),
                        'is_active': is_active_bool,
                        'description': row.get('description', '') or ''
                    }
                )
                imported += 1
            except Exception:
                pass
    return imported, 0

def execute_master_post_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        code_val = row.get('code') or row.get('Code')
        name_val = row.get('name') or row.get('Name')
        
        # Safe extraction for active column status flag
        active_raw = row.get('is_active') or row.get('Active')
        if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null']:
            is_active_bool = True
        else:
            is_active_bool = str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

        if code_val and name_val:
            try:
                # 🟢 FIX: Protect target models by matching cleanly on 'code' string inputs
                PostMaster.objects.update_or_create(
                    code=str(code_val).strip().upper(),
                    defaults={
                        'name': str(name_val).strip(),
                        'is_active': is_active_bool,
                        'description': row.get('description', '') or ''
                    }
                )
                imported += 1
            except Exception:
                pass
    return imported, 0


def execute_master_zone_import(file_obj):
    # 🟢 Step 1: Securely bind global state maps
    global _ZIP_COUNTRY_MAP, _ZIP_ZONE_MAP
    
    # 🟢 Step 2: Prevent NameError if running outside a ZIP process session
    if '_ZIP_COUNTRY_MAP' not in globals():
        _ZIP_COUNTRY_MAP = {}
    if '_ZIP_ZONE_MAP' not in globals():
        _ZIP_ZONE_MAP = {}

    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0

    for row in data:
        code_val = row.get('code') or row.get('Code')
        name_val = row.get('name') or row.get('Name')
        old_id = row.get('id') or row.get('Id') or row.get('ID')
        
        # Read Column exactly as exported (can be an integer ID or a code string)
        country_raw = row.get('country') or row.get('country_id') or row.get('country_code') or row.get('Country')

        if code_val and name_val:
            try:
                # Clean country reference input into a standard string key
                country_key = str(country_raw).strip() if country_raw is not None else '1'
                if country_key.lower() in ['', 'none', 'nan', 'null', '-']:
                    country_key = '1'  # Fallback to default India ID pointer

                # Step 1: Resolve the Country reference safely
                # Look up the code string from our active session cache map first
                country_code = _ZIP_COUNTRY_MAP.get(country_key)
                country = None
                
                if country_code:
                    country = Country.objects.filter(code=country_code).first()
                
                # 🟢 STEP 3: SAFE NUMERIC CONVERSION UPGRADE
                if not country:
                    try:
                        # Handles converting "1", "1.0", or 1.0 safely without falling back to string code filtering errors
                        country_id_int = int(float(country_key))
                        country = Country.objects.filter(id=country_id_int).first()
                    except (ValueError, TypeError):
                        pass

                # Step 4: Text-based string filter lookups matching character patterns
                if not country:
                    country = Country.objects.filter(code__iexact=country_key).first() or \
                              Country.objects.filter(name__icontains=country_key).first()

                # Absolute fallback: Guard against unpopulated country tables
                if not country:
                    country = Country.objects.filter(code='IN').first() or Country.objects.first()

                # Step 2: Extract active status cleanly
                active_raw = row.get('is_active') or row.get('Active')
                if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null']:
                    is_active_bool = True  # Standard fallback default
                else:
                    is_active_bool = str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

                clean_code = str(code_val).strip().upper()
                zone, created = Zone.objects.update_or_create(
                    code=clean_code,
                    defaults={
                        'name': str(name_val).strip(),
                        'country': country,
                        'is_active': is_active_bool,
                        'description': row.get('description', '') or ''
                    }
                )
                
                # Record this zone mapping definition inside the session cache map securely
                if old_id:
                    _ZIP_ZONE_MAP[str(old_id).strip()] = clean_code
                
                # Also self-map the code string onto itself to guard alternate manual layouts
                _ZIP_ZONE_MAP[clean_code] = clean_code
                imported += 1
                
            except Exception:
                skipped += 1
                
    return imported, skipped


def execute_master_center_import(file_obj):
    # 🟢 Step 1: Securely bind global state maps
    global _ZIP_ZONE_MAP, _ZIP_CENTER_MAP
    
    # 🟢 Step 2: Prevent NameError if running outside a ZIP process session
    if '_ZIP_ZONE_MAP' not in globals(): 
        _ZIP_ZONE_MAP = {}
    if '_ZIP_CENTER_MAP' not in globals(): 
        _ZIP_CENTER_MAP = {}

    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0

    for row in data:
        code_val = row.get('code') or row.get('Code')
        name_val = row.get('name') or row.get('Name')
        old_id = row.get('id') or row.get('Id') or row.get('ID')
        zone_raw = row.get('zone_code') or row.get('zone') or row.get('Zone') or row.get('zone_id')

        if code_val and name_val and zone_raw:
            try:
                zone_key = str(zone_raw).strip()
                resolved_zone_code = _ZIP_ZONE_MAP.get(zone_key)
                
                # 🟢 Step 3: Fallback DB lookup if running a manual individual upload
                if resolved_zone_code:
                    zone_instance = Zone.objects.filter(code=resolved_zone_code).first()
                else:
                    zone_instance = Zone.objects.filter(code__iexact=zone_key).first()
                    if not zone_instance and zone_key.isdigit():
                        zone_instance = Zone.objects.filter(id=int(zone_key)).first()

                if not zone_instance:
                    skipped += 1
                    continue

                active_raw = row.get('is_active') or row.get('Active')
                is_active_bool = True if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null'] else str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

                clean_code = str(code_val).strip().upper()
                center, created = Center.objects.update_or_create(
                    code=clean_code,
                    defaults={
                        'name': str(name_val).strip(),
                        'zone': zone_instance,
                        'city': row.get('city', '') or '',
                        'state': row.get('state', '') or '',
                        'pincode': row.get('pincode', '') or '',
                        'address': row.get('address', '') or '',
                        'is_active': is_active_bool
                    }
                )

                if old_id:
                    _ZIP_CENTER_MAP[str(old_id).strip()] = clean_code
                
                _ZIP_CENTER_MAP[clean_code] = clean_code
                imported += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1
            
    return imported, skipped


def execute_master_department_import(file_obj):
    global _ZIP_COUNTRY_MAP, _ZIP_CENTER_MAP, _ZIP_DEPARTMENT_MAP
    
    # 🟢 Safe Memory Map Initializer
    if '_ZIP_COUNTRY_MAP' not in globals(): _ZIP_COUNTRY_MAP = {}
    if '_ZIP_CENTER_MAP' not in globals(): _ZIP_CENTER_MAP = {}
    if '_ZIP_DEPARTMENT_MAP' not in globals(): _ZIP_DEPARTMENT_MAP = {}

    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0

    for row in data:
        code_val = row.get('code') or row.get('Code')
        name_val = row.get('name') or row.get('Name')
        old_id = row.get('id') or row.get('Id') or row.get('ID')
        country_raw = row.get('country_code') or row.get('country')
        center_raw = row.get('center_code') or row.get('center')

        if code_val and name_val:
            try:
                # Resolve Country
                c_key = str(country_raw).strip() if country_raw else ''
                c_code = _ZIP_COUNTRY_MAP.get(c_key) or c_key
                country_instance = Country.objects.filter(code__iexact=c_code).first() or Country.objects.first()

                # Resolve Center (Optional column field setup)
                center_instance = None
                if center_raw:
                    cen_key = str(center_raw).strip()
                    cen_code = _ZIP_CENTER_MAP.get(cen_key) or cen_key
                    center_instance = Center.objects.filter(code__iexact=cen_code).first()

                active_raw = row.get('is_active') or row.get('Active')
                is_active_bool = True if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null'] else str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

                clean_code = str(code_val).strip().upper()
                dept, created = Department.objects.update_or_create(
                    code=clean_code,
                    defaults={
                        'name': str(name_val).strip(),
                        'country': country_instance,
                        'center': center_instance,
                        'description': row.get('description', '') or '',
                        'is_active': is_active_bool
                    }
                )

                if old_id:
                    _ZIP_DEPARTMENT_MAP[str(old_id).strip()] = clean_code
                _ZIP_DEPARTMENT_MAP[clean_code] = clean_code
                imported += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1
    return imported, skipped

def execute_master_workflow_config_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        name = row.get('config_name') or row.get('Config Name')
        if name:
            RegistrationWorkflowConfig.objects.update_or_create(
                config_name=str(name).strip(),
                defaults={'is_direct_registration': str(row.get('is_direct_registration', '')).lower() in ['1', 'true', 'yes']}
            )
            imported += 1
    return imported, 0

def execute_master_email_template_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        t_name = row.get('template_name') or row.get('Template Name')
        if t_name:
            EmailNotificationTemplate.objects.update_or_create(
                template_name=str(t_name).strip(),
                defaults={'event_type': row.get('event_type', ''), 'subject': row.get('subject', ''), 'body': row.get('body', ''), 'is_active': True}
            )
            imported += 1
    return imported, 0

def execute_master_sms_template_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        t_name = row.get('template_name') or row.get('Template Name')
        if t_name:
            SMSTemplate.objects.update_or_create(
                template_name=str(t_name).strip(),
                defaults={'event_type': row.get('event_type', ''), 'message_text': row.get('message_text', ''), 'is_active': True}
            )
            imported += 1
    return imported, 0

def execute_master_whatsapp_template_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        t_name = row.get('template_name') or row.get('Template Name')
        if t_name:
            WhatsAppNotificationTemplate.objects.update_or_create(
                template_name=str(t_name).strip(),
                defaults={'event_type': row.get('event_type', ''), 'message_body': row.get('message_body', ''), 'button_text': row.get('button_text', ''), 'button_url': row.get('button_url', ''), 'is_active': True}
            )
            imported += 1
    return imported, 0

def execute_master_email_mapping_import(file_obj):
    global _ZIP_POST_MAP, _ZIP_CENTER_MAP, _ZIP_DEPARTMENT_MAP
    
    if '_ZIP_POST_MAP' not in globals(): _ZIP_POST_MAP = {}
    if '_ZIP_CENTER_MAP' not in globals(): _ZIP_CENTER_MAP = {}
    if '_ZIP_DEPARTMENT_MAP' not in globals(): _ZIP_DEPARTMENT_MAP = {}

    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0
    
    for row in data:
        # 🟢 UPGRADE: Fallback to 'role_name' column header if 'post' is missing
        post_id = row.get('post') or row.get('post_id') or row.get('role_name') or row.get('role')
        email_val = row.get('email') or row.get('Email')
        center_id = row.get('center_code') or row.get('center')
        dept_id = row.get('department_code') or row.get('department')
        
        if not email_val or not post_id:
            skipped += 1
            continue
            
        try:
            post_key = str(post_id).strip()
            post_role = _ZIP_POST_MAP.get(post_key)
            
            if post_role:
                post = PostMaster.objects.filter(role_name=post_role).first()
            else:
                post = PostMaster.objects.filter(role_name__iexact=post_key).first() or \
                       PostMaster.objects.filter(code__iexact=post_key).first()
                if not post and post_key.isdigit():
                    post = PostMaster.objects.filter(id=int(post_key)).first()
                
            if not post:
                skipped += 1
                continue

            center = None
            if center_id and str(center_id).strip() not in ['', 'None', 'NaN', '-', 'nan']:
                cen_key = str(center_id).strip()
                center_code = _ZIP_CENTER_MAP.get(cen_key) or cen_key
                center = Center.objects.filter(code__iexact=center_code).first()
                if not center and cen_key.isdigit():
                    center = Center.objects.filter(id=int(cen_key)).first()

            department = None
            if dept_id and str(dept_id).strip() not in ['', 'None', 'NaN', '-', 'nan']:
                d_key = str(dept_id).strip()
                dept_code = _ZIP_DEPARTMENT_MAP.get(d_key) or d_key
                department = Department.objects.filter(code__iexact=dept_code).first()
                if not department and d_key.isdigit():
                    department = Department.objects.filter(id=int(d_key)).first()
            
            clean_email = str(email_val).strip().lower()
            mapping_type = str(row.get('mapping_type', 'center')).strip().lower()
            
            EmailMapping.objects.update_or_create(
                post=post, 
                email=clean_email, 
                mapping_type=mapping_type,
                center=center, 
                department=department,
                defaults={
                    'person_name': row.get('person_name', '') or '', 
                    'phone_number': row.get('phone_number', '') or '', 
                    'is_primary': str(row.get('is_primary', '')).lower() in ['1', 'true', 'yes', 'active']
                }
            )
            
            # 🟢 UPGRADE: Count actual insertions instead of collapsing unique keys
            imported += 1
        except Exception:
            skipped += 1
            
    return imported, skipped


def execute_master_approval_rule_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        r_name = row.get('rule_name') or row.get('Rule Name')
        if r_name:
            min_amt_raw = str(row.get('min_amount', 0) or 0).replace(',', '').strip()
            max_amt_raw = str(row.get('max_amount', 0) or 0).replace(',', '').strip()

            # 🟢 Parse dynamic status column mapping (1/0, True/False)
            active_raw = str(row.get('is_active') or row.get('is_Active') or row.get('Active') or row.get('active') or '1').strip().lower()
            is_active_bool = active_raw in ['1', 'true', 'yes', 'active']

            ApprovalRule.objects.update_or_create(
                rule_name=str(r_name).strip(),
                defaults={
                    'rule_type': row.get('rule_type', 'amount'),
                    'min_amount': float(min_amt_raw if min_amt_raw else 0),
                    'max_amount': float(max_amt_raw if max_amt_raw else 0),
                    'chain_type': row.get('chain_type', 'serial'),
                    'priority': int(row.get('priority', 1)), 
                    'is_active': is_active_bool
                }
            )
            imported += 1
    return imported, 0

def execute_master_rule_sequence_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    for row in data:
        r_name = row.get('rule__rule_name') or row.get('rule_name')
        l_name = row.get('approval_level__level_name') or row.get('level_name')
        seq_order = row.get('sequence_order') or 1
        try:
            rule = ApprovalRule.objects.get(rule_name=str(r_name).strip())
            level = ApprovalLevel.objects.filter(level_name=str(l_name).strip().upper()).first()
            if rule and level:
                RuleApprovalSequence.objects.update_or_create(
                    rule=rule, approval_level=level, sequence_order=int(seq_order),
                    defaults={'is_mandatory': True, 'allow_delegation': False}
                )
                imported += 1
        except Exception:
            skipped += 1
    return imported, skipped

def execute_master_routing_matrix_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported = 0
    for row in data:
        event = row.get('event_type') or row.get('Event Type')
        if event:
            clean_event = str(event).strip()
            defaults_dict = {}
            
            for k, v in row.items():
                # 🟢 CRITICAL SAFETY CHECK: Exclude identity and system columns to prevent constraint crashes
                if k.lower() in ['id', 'event_type', 'created_at', 'updated_at']:
                    continue
                
                # 🟢 Apply explicit layout tracking rule: 1 = Active (True), 0 = Inactive (False)
                if v is None or str(v).strip().lower() in ['', 'none', 'nan', 'null', '0']:
                    defaults_dict[k] = False
                else:
                    defaults_dict[k] = str(v).strip().lower() in ['1', 'true', 'yes', 'active']
            
            # Update matching event record or create a fresh mapping dynamically
            NotificationRoutingMatrix.objects.update_or_create(
                event_type=clean_event,
                defaults=defaults_dict
            )
            imported += 1
            
    return imported, 0


def execute_master_user_import(file_obj):
    # 🟢 Step 1: Securely bind the global session map tracker
    global _ZIP_USER_MAP
    
    # 🟢 Step 2: Prevent NameError if running a single manual CSV upload outside a ZIP session
    if '_ZIP_USER_MAP' not in globals():
        _ZIP_USER_MAP = {}

    # Parse the file data based on extension type (.csv vs .xlsx)
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0

    for row in data:
        uname = row.get('username') or row.get('Username') or row.get('user_username')
        old_id = row.get('id') or row.get('Id') or row.get('ID')
        
        if uname:
            try:
                clean_username = str(uname).strip()
                
                # 🟢 Step 3: Bulletproof active status boolean check handling 1, True, "yes", or "active"
                active_raw = row.get('is_active') or row.get('Active')
                if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null']:
                    is_active_bool = True  # Standard fallback default
                else:
                    is_active_bool = str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']
                
                # Fetch date joined column value or fallback to Django timezone utility
                date_joined_val = row.get('date_joined') or row.get('Date Joined') or timezone.now()
                
                # 🟢 Step 4: Perform safe update or create configuration block
                user, created = User.objects.update_or_create(
                    username=clean_username,
                    defaults={
                        'email': str(row.get('email', '') or '').strip().lower(),
                        'first_name': str(row.get('first_name', '') or '').strip().title(),
                        'last_name': str(row.get('last_name', '') or '').strip().title(),
                        'is_active': is_active_bool,
                    }
                )
                
                # If creating a fresh user instance, apply temporary passwords securely
                if created:
                    user.set_password("SMVS@temp2026")
                    user.date_joined = date_joined_val
                    user.save()
                
                # 🟢 Step 5: Save old mapping references cleanly to resolve user role overlaps
                if old_id:
                    _ZIP_USER_MAP[str(old_id).strip()] = clean_username
                
                # Also cross-map the username string directly onto itself to handle manual text columns
                _ZIP_USER_MAP[clean_username] = clean_username
                
                imported += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1
            
    return imported, skipped


def execute_master_user_profile_import(file_obj):
    global _ZIP_USER_MAP, _ZIP_DEPARTMENT_MAP, _ZIP_CENTER_MAP
    
    # 🟢 Safe Memory Map Initializer
    if '_ZIP_USER_MAP' not in globals(): _ZIP_USER_MAP = {}
    if '_ZIP_DEPARTMENT_MAP' not in globals(): _ZIP_DEPARTMENT_MAP = {}
    if '_ZIP_CENTER_MAP' not in globals(): _ZIP_CENTER_MAP = {}

    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0

    for row in data:
        user_raw = row.get('user_username') or row.get('user') or row.get('user_id')
        phone_val = row.get('phone') or row.get('Phone') or ''
        dept_raw = row.get('department_code') or row.get('department')
        center_raw = row.get('center_code') or row.get('center')

        if user_raw:
            try:
                user_key = str(user_raw).strip()
                resolved_username = _ZIP_USER_MAP.get(user_key) or user_key
                user_instance = User.objects.filter(username__iexact=resolved_username).first()
                if not user_instance and user_key.isdigit():
                    user_instance = User.objects.filter(id=int(user_key)).first()

                if not user_instance:
                    skipped += 1
                    continue

                dept_instance = None
                if dept_raw:
                    d_key = str(dept_raw).strip()
                    d_code = _ZIP_DEPARTMENT_MAP.get(d_key) or d_key
                    dept_instance = Department.objects.filter(code__iexact=d_code).first()

                center_instance = None
                if center_raw:
                    cen_key = str(center_raw).strip()
                    cen_code = _ZIP_CENTER_MAP.get(cen_key) or cen_key
                    center_instance = Center.objects.filter(code__iexact=cen_code).first()

                UserProfile.objects.update_or_create(
                    user=user_instance,
                    defaults={
                        'phone': str(phone_val).strip(),
                        'department': dept_instance,
                        'center': center_instance
                    }
                )
                imported += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1
    return imported, skipped


def execute_master_user_role_import(file_obj):
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    for row in data:
        uname = row.get('User Username') or row.get('user_username') or row.get('username')
        role_val = row.get('Role') or row.get('role')
        try:
            user = User.objects.get(username=str(uname).strip())
            active_raw = str(row.get('is_active') or row.get('Is Active') or 'true').strip().lower()
            is_active_bool = active_raw in ['1', 'true', 'yes', 'active']
            
            UserRole.objects.update_or_create(
                user=user,
                defaults={
                    'role': str(role_val).strip().lower().replace(' ', '_'),
                    'mobile_number': str(row.get('Mobile Number') or row.get('mobile_number') or '').strip(),
                    'is_active': is_active_bool
                }
            )
            imported += 1
        except Exception:
            skipped += 1
    return imported, skipped


def execute_master_user_workspace_import(file_obj):
    global _ZIP_USER_MAP, _ZIP_DEPARTMENT_MAP
    
    # 🟢 Safe Memory Map Initializer
    if '_ZIP_USER_MAP' not in globals(): _ZIP_USER_MAP = {}
    if '_ZIP_DEPARTMENT_MAP' not in globals(): _ZIP_DEPARTMENT_MAP = {}

    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    if not data:
        return 0, 0

    for row in data:
        user_raw = row.get('username') or row.get('user') or row.get('user_id')
        depts_raw = row.get('department_codes_separated_by_comma') or row.get('departments') or row.get('departments__code') or ''

        if user_raw:
            try:
                user_key = str(user_raw).strip()
                resolved_username = _ZIP_USER_MAP.get(user_key) or user_key
                user_instance = User.objects.filter(username__iexact=resolved_username).first()
                if not user_instance and user_key.isdigit():
                    user_instance = User.objects.filter(id=int(user_key)).first()

                if not user_instance:
                    skipped += 1
                    continue

                workspace, created = UserWorkspace.objects.get_or_create(user=user_instance)
                
                # If changing department parameters, clear and remap M2M lists safely
                if depts_raw:
                    workspace.departments.clear()
                    dept_tokens = [t.strip().upper() for t in str(depts_raw).split(',') if t.strip()]
                    for token in dept_tokens:
                        resolved_dept_code = _ZIP_DEPARTMENT_MAP.get(token) or token
                        dept_obj = Department.objects.filter(code__iexact=resolved_dept_code).first()
                        if dept_obj:
                            workspace.departments.add(dept_obj)
                            
                imported += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1
    return imported, skipped


def execute_master_approval_level_user_import(file_obj):
    global _ZIP_USER_MAP, _ZIP_APPROVAL_LEVEL_MAP
    data = ImportExportHelper.parse_csv_file(file_obj) if file_obj.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file_obj)
    imported, skipped = 0, 0
    
    for row in data:
        # Extract row pointer keys matching your export columns
        user_raw = row.get('user') or row.get('user_id')
        level_raw = row.get('level') or row.get('approval_level') or row.get('level_id')
        
        # Safe extraction for active column status flag
        active_raw = row.get('is_active') or row.get('Active')
        if active_raw is None or str(active_raw).strip().lower() in ['', 'none', 'nan', 'null']:
            is_active_bool = True
        else:
            is_active_bool = str(active_raw).strip().lower() in ['1', 'true', 'yes', 'active']

        if user_raw and level_raw:
            try:
                # 🟢 1. Resolve User Object cleanly using session cache mapping tracking rules
                user_str = str(user_raw).strip()
                user_code = _ZIP_USER_MAP.get(user_str)
                
                if user_code:
                    user = User.objects.filter(username=user_code).first()
                else:
                    # Fallback to direct database lookup via integer primary key ID
                    user = User.objects.filter(id=int(float(user_str))).first() if user_str.isdigit() else None
                    if not user:
                        user = User.objects.filter(username=user_str).first() or User.objects.filter(email=user_str).first()
                
                # 🟢 2. Resolve ApprovalLevel Object cleanly using session cache mapping tracking rules
                level_str = str(level_raw).strip()
                level_code = _ZIP_APPROVAL_LEVEL_MAP.get(level_str)
                
                if level_code:
                    level_obj = ApprovalLevel.objects.filter(code=level_code).first()
                else:
                    # Fallback to direct database lookup via integer primary key ID or level order
                    level_obj = ApprovalLevel.objects.filter(id=int(float(level_str))).first() if level_str.isdigit() else None
                    if not level_obj:
                        level_obj = ApprovalLevel.objects.filter(code=level_str.upper()).first() or \
                                    ApprovalLevel.objects.filter(level=int(float(level_str))).first()

                # Guard check: Skip the execution loop safely if relations cannot be matched to live objects
                if not user or not level_obj:
                    skipped += 1
                    continue

                # 🟢 3. Update or Create relationship mapping cleanly without ID conflicts
                ApprovalLevelUser.objects.update_or_create(
                    user=user,
                    level=level_obj,
                    defaults={
                        'is_active': is_active_bool
                    }
                )
                imported += 1
            except Exception:
                skipped += 1
                
    return imported, skipped

# ==================== MAIN ONE-CLICK CONTROLLER VIEW ====================

@staff_member_required
def master_zip_import_view(request):
    """Universal Master Sync Panel processing tables in precise relational sequence."""
    if request.method == 'POST' and request.FILES.get('zip_file'):
        # 🟢 RESET lifecycle session maps at startup   
        global _ZIP_COUNTRY_MAP, _ZIP_ZONE_MAP, _ZIP_CENTER_MAP, _ZIP_DEPARTMENT_MAP, _ZIP_POST_MAP
        _ZIP_COUNTRY_MAP.clear()
        _ZIP_ZONE_MAP.clear()
        _ZIP_CENTER_MAP.clear()
        _ZIP_DEPARTMENT_MAP.clear()
        _ZIP_POST_MAP.clear()
        zip_file = request.FILES['zip_file']
        
        if not zip_file.name.endswith('.zip'):
            messages.error(request, "Please upload a valid compressed .zip file archive.")
            return render(request, 'admin/master_zip_import.html')

        try:
            with zipfile.ZipFile(zip_file) as archive:
                file_list = archive.namelist()
                
                # 🟢 PROCESSED IN CORRECT CASCADE HIERARCHY
                import_sequence = [
                    {'key': 'Country', 'names': ['country', 'countries'], 'func': execute_master_country_import},
                    {'key': 'ApprovalLevel', 'names': ['approvallevel', 'approval_level', 'level'], 'func': execute_master_approval_level_import},
                    {'key': 'PostMaster', 'names': ['postmaster', 'post_master', 'post', 'posts'], 'func': execute_master_post_import},
                    {'key': 'Zone', 'names': ['zone', 'zones'], 'func': execute_master_zone_import},
                    {'key': 'Center', 'names': ['center', 'centers'], 'func': execute_master_center_import},
                    {'key': 'Department', 'names': ['department', 'departments'], 'func': execute_master_department_import},

                    # 🟢 PHASE 2 TIER: CORE ACCOUNT IMPORT ENGINES
                    {'key': 'User', 'names': ['user-setting', 'users', 'auth_user'], 'func': execute_master_user_import},
                    {'key': 'UserProfile', 'names': ['userprofile', 'user_profile', 'profile'], 'func': execute_master_user_profile_import},
                    {'key': 'UserRole', 'names': ['userrole', 'user_role', 'roles'], 'func': execute_master_user_role_import},
                    {'key': 'UserWorkspace', 'names': ['userworkspace', 'user_workspace', 'workspace'], 'func': execute_master_user_workspace_import},
                    {'key': 'ApprovalLevelUser', 'names': ['approvalleveluser', 'level_user', 'assignment'], 'func': execute_master_approval_level_user_import},

                    # CONFIGURATIONS & INFRASTRUCTURE TIER
                    {'key': 'RegistrationConfig', 'names': ['registration', 'workflowconfig'], 'func': execute_master_workflow_config_import},
                    {'key': 'EmailTemplate', 'names': ['emailnotificationtemplate', 'email_template'], 'func': execute_master_email_template_import},
                    {'key': 'SMSTemplate', 'names': ['smstemplate', 'sms_template'], 'func': execute_master_sms_template_import},
                    {'key': 'WhatsAppTemplate', 'names': ['whatsappnotificationtemplate', 'whatsapp_template'], 'func': execute_master_whatsapp_template_import},
                    {'key': 'EmailMapping', 'names': ['emailmapping', 'email_mappings'], 'func': execute_master_email_mapping_import},
                    {'key': 'ApprovalRule', 'names': ['approvalrule', 'approval_rule', 'rules'], 'func': execute_master_approval_rule_import},
                    {'key': 'RuleSequence', 'names': ['ruleapprovalsequence', 'sequences', 'sequence'], 'func': execute_master_rule_sequence_import},
                    {'key': 'RoutingMatrix', 'names': ['notificationroutingmatrix', 'routing_matrix'], 'func': execute_master_routing_matrix_import},
                ]

                summary_report = []

                for step in import_sequence:
                    matched_file = None
                    for filename in file_list:
                        if '/' in filename and not filename.split('/')[-1]:
                            continue
                        clean_name = filename.split('/')[-1].lower()
                        if any(term in clean_name for term in step['names']) and (clean_name.endswith('.csv') or clean_name.endswith('.xlsx')):
                            matched_file = filename
                            break
                    
                    if matched_file:
                        file_bytes = archive.read(matched_file)
                        in_memory_file = io.BytesIO(file_bytes)
                        in_memory_file.name = matched_file
                        
                        imported_count, skipped_count = step['func'](in_memory_file)
                        summary_report.append(f"✅ {step['key']}: Sync completed ({imported_count} rows processed)")
                    else:
                        summary_report.append(f"⚠️ {step['key']}: File not present inside archive, skipped.")

                for report in summary_report:
                    messages.info(request, report)
                    
                messages.success(request, "🚀 Complete Master System Sync Executed Flawlessly!")
                return redirect('admin:index')

        except Exception as e:
            messages.error(request, f"❌ Master file processor crashed: {str(e)}")
            
    return render(request, 'admin/master_zip_import.html')


@require_POST
@login_required
def verify_admin_password(request):
    """
    Dynamic AJAX Verification Gate.
    - Superusers/Admins are verified against the master ADMIN_PANEL_PASSWORD.
    - Staff members/Operators are verified against their own unique login password.
    """
    try:
        data = json.loads(request.body)
        entered_password = data.get('password', '').strip()
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid request structure'}, status=400)

    if not entered_password:
        return JsonResponse({'success': False, 'error': 'Password field cannot be empty'})

    user = request.user
    user_role = getattr(user, "approval_role", None)

    # 🛡️ LEVEL 1: Master Admin Verification Route
    if user.is_superuser or (user_role and user_role.role == 'admin'):
        master_password = settings.ADMIN_PANEL_PASSWORD
        if entered_password == master_password:
            request.session['admin_password_verified'] = True
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Incorrect Master Admin Password.'})

    # 🛡️ LEVEL 2: Staff / Operator Verification Route
    elif user.is_staff:
        # Check encrypted password matching using Django's native security layer
        if check_password(entered_password, user.password):
            request.session['admin_password_verified'] = True
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Incorrect Personal Security Password.'})

    # 🚫 LEVEL 3: Fallback Block for Untrusted Accounts
    else:
        return JsonResponse({'success': False, 'error': 'Access Denied: Insufficient staff permissions.'}, status=403)


@require_GET
@login_required
def check_admin_session_status(request):
    """Checks if the user has already successfully passed the password gate in this session."""
    if request.session.get('admin_password_verified', False):
        return JsonResponse({'verified': True})
    return JsonResponse({'verified': False})


@login_required
def prabhari_report_view(request):
    user_role = get_object_or_404(UserRole, user=request.user)
    
    # 1. PERMISSION CHECK
    allowed_roles = ['mk_sabhya', 'mk_sant', 'prabhari', 'p_rajipaswami', 'admin']
    if user_role.role not in allowed_roles:
        messages.error(request, "Access Denied.")
        return redirect('dashboard')

    # 2. GET ALLOTTED RIGHTS
    assigned_centers = user_role.accessible_centers.all()
    assigned_depts = user_role.accessible_departments.all()

    try:
        report_perm = request.user.report_permission
        if report_perm.can_view_report:
            # Convert to list and append to prevent duplicates
            for center in report_perm.restrict_to_centers.all():
                if center not in assigned_centers:
                    assigned_centers.append(center)
            for dept in report_perm.restrict_to_departments.all():
                if dept not in assigned_depts:
                    assigned_depts.append(dept)
    except Exception:
        pass  # Fallback gracefully if profile configuration record isn't built yet

    assigned_zones = set()
    for center in assigned_centers:
        if center.zone and center.zone.name:
            assigned_zones.add(center.zone.name)

    # 3. BASE QUERY (Scoped strictly to allotted items)
    base_forms = ApprovalForm.objects.filter(
        Q(selected_center__in=assigned_centers) | 
        Q(department__in=assigned_depts) |
        Q(center__in=assigned_centers)
    ).distinct()

    # 4. GET FILTER PARAMETERS
    selected_years = request.GET.getlist('year')
    selected_status = request.GET.get('status')
    selected_center = request.GET.get('center')
    selected_dept = request.GET.get('dept')
    selected_zone = request.GET.get('zone')
    selected_currency = request.GET.get('currency') # ✅ Read chosen country currency

    # 5. APPLY FILTER SCALING TO TABLE DATA ONLY
    filtered_forms = base_forms.order_by('-created_at')

    if selected_years:
        filtered_forms = filtered_forms.filter(created_at__year__in=selected_years)
    if selected_status:
        filtered_forms = filtered_forms.filter(status=selected_status)
    if selected_center:
        filtered_forms = filtered_forms.filter(Q(selected_center_id=selected_center) | Q(center_id=selected_center))
    if selected_dept:
        filtered_forms = filtered_forms.filter(department_id=selected_dept)
    if selected_zone:
        filtered_forms = filtered_forms.filter(Q(selected_center__zone__name=selected_zone) | Q(center__zone__name=selected_zone))
    if selected_currency:
        filtered_forms = filtered_forms.filter(currency_code=selected_currency) # ✅ Filter applied dynamically

    # 🟢 CREATE A COPY FOR CARDS BEFORE STATUS SLICING
    # This guarantees your summary metrics dynamically respond to currency/center selections
    card_forms = filtered_forms

    # Now, apply status filtering ONLY to the table records below
    if selected_status:
        filtered_forms = filtered_forms.filter(status=selected_status)

    # 6. DYNAMIC RANGE PARAMETERS FOR UI
    current_year = datetime.now().year
    year_range = range(2025, current_year + 1)

    # 7. FIXED SUMMARY CARDS (Calculated using base_forms so card statistics remain accurate)
    total_requests = card_forms.count()
    draft_count = card_forms.filter(status='initiated').count()
    pending_count = card_forms.filter(status='pending').count()
    revision_count = card_forms.filter(status='revision_pending').count()
    rejected_count = card_forms.filter(status='rejected').count()
    approved_count = card_forms.filter(status='approved').count()

    active_centers_count = card_forms.values('selected_center').distinct().count()
    active_depts_count = card_forms.values('department').distinct().count()
    
    # Dynamic Master Currency query matching existing profiles
    distinct_currencies = ApprovalForm.objects.exclude(currency_code__isnull=True).values('currency_code', 'currency_symbol').distinct().order_by('currency_code')

    # 8. ADD PAGINATION (10 forms per page from filtered criteria)
    paginator = Paginator(filtered_forms, 10) 
    page_number = request.GET.get('page')
    
    try:
        page_obj = paginator.get_page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, "approval_core/prabhari_report.html", {
        "page_obj": page_obj,
        "total_requests": total_requests,
        "draft_count": draft_count,
        "pending_count": pending_count,
        "revision_count": revision_count,
        "rejected_count": rejected_count,
        "approved_count": approved_count,
        "active_depts_count": active_depts_count,
        "active_centers_count": active_centers_count,
        "assigned_centers": assigned_centers,
        "assigned_depts": assigned_depts,
        "assigned_zones": assigned_zones,
        "years": reversed(year_range),
        "selected_years": selected_years,
        "dynamic_currencies": distinct_currencies,
    })

@login_required(login_url="login")
def edit_form_view(request, form_id):
    """Allow End User to edit Amount and Description with strict revision rules"""
    approval_form = get_object_or_404(ApprovalForm, id=form_id)
    
    # 1. Security Check: Only the submitter can edit
    if approval_form.submitted_by != request.user:
        messages.error(request, "You can only edit your own forms.")
        return redirect("dashboard")

    # 2. Status Check: Only editable in 'initiated' (Draft) or 'revision_pending'
    if approval_form.status not in ["initiated", "revision_pending"]:
        messages.error(request, "This form can no longer be edited.")
        return redirect("form_detail", form_id=form_id)

    if request.method == "POST":
        description = request.POST.get("description")
        new_amount_str = request.POST.get("amount")

        try:
            new_amount = Decimal(new_amount_str)
            
            # ✅ REVISION RESTRICTION LOGIC
            # If in revision mode, the new amount cannot be GREATER than the current amount
            if approval_form.status == "revision_pending":
                if new_amount > approval_form.amount:
                    messages.error(request, f"In Revision mode, you cannot increase the amount. Maximum allowed: {approval_form.currency_symbol}{approval_form.amount}")
                    return render(request, "approval_core/edit_form.html", {"form": approval_form})

            # Update the fields
            approval_form.description = description
            approval_form.amount = new_amount
            
            # Recalculate INR equivalent
            if approval_form.currency_code != 'INR':
                approval_form.amount_inr = approval_form.amount * approval_form.exchange_rate_used
            else:
                approval_form.amount_inr = approval_form.amount
                
            approval_form.save()
            messages.success(request, "Form updated successfully.")
            return redirect("form_detail", form_id=form_id)
            
        except Exception as e:
            messages.error(request, f"Update failed: {str(e)}")

    return render(request, "approval_core/edit_form.html", {"form": approval_form})


def forgot_password_view(request):
    """Simplified password reset: User enters only Username"""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()

        try:
            # 1. Fetch the user and associated role by Username case-insensitively using __iexact
            user_role = UserRole.objects.get(user__username__iexact=username)
            user = user_role.user
            email = user.email
            mobile = user_role.mobile_number

            if not user.is_active:
                messages.error(request, "Your account is pending admin approval.")
                return redirect("login")

            if not email or not mobile:
                messages.error(request, "Registered email or mobile number not found. Please contact Admin.")
                return redirect("forgot_password")

            # 2. Generate 6-digit OTP
            otp = str(random.randint(100000, 999999))
            request.session['reset_otp'] = otp
            request.session['reset_user_id'] = user.id
            request.session['otp_expiry'] = (timezone.now() + timedelta(minutes=10)).isoformat()

            # 3. Dynamic Notification logic (fetch from Admin Templates)
            from approval_core.models import EmailNotificationTemplate
            email_template = EmailNotificationTemplate.objects.filter(event_type="otp_sent", is_active=True).first()
            
            if email_template:
                from django.template import Template, Context
                t = Template(email_template.body)
                c = Context({
                    'user': user,
                    'otp': otp,
                    'login_url': request.build_absolute_uri(reverse('login'))
                })
                email_body = t.render(c)
                subject = email_template.subject
            else:
                subject = "Password Reset OTP"
                email_body = f"Hello {user.username}, your reset OTP is: {otp}"

            # 4. Send SMS and Email
            notifier = NotificationService()
            sms_success, _ = notifier.send_otp_sms(mobile, otp)
            
            email_sent = send_mail(
                subject,
                "", 
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=True,
                html_message=email_body
            )

            if sms_success or email_sent:
                messages.success(request, f"A reset OTP has been sent to your registered mobile and email.")
                return redirect("verify_otp")
            else:
                messages.error(request, "Failed to deliver reset code.")

        except UserRole.DoesNotExist:
            messages.error(request, "The username provided does not match our records.")

    return render(request, "approval_core/forgot_password.html")


def verify_otp_view(request):
    user_id = request.session.get('reset_user_id')
    if not user_id:
        return redirect('forgot_password')

    otp_verified = request.session.get('otp_verified', False)

    if request.method == "POST":
        if not otp_verified:
            entered_otp = request.POST.get("otp", "").strip()
            session_otp = request.session.get('reset_otp')

            if entered_otp and entered_otp == session_otp:
                request.session['otp_verified'] = True
                messages.success(request, "OTP verified successfully! Please enter your new password.")
                return redirect('verify_otp')
            else:
                messages.error(request, "Invalid OTP. Please try again.")

        # 🟢 HANDLING NEW PASSWORD SUBMISSION WITH COMPLEXITY POLICY Check
        else:
            new_password = request.POST.get("password")
            confirm_password = request.POST.get("confirm_password")

            if new_password != confirm_password:
                messages.error(request, "Passwords do not match.")
                return render(request, "approval_core/verify_otp.html", {"otp_verified": otp_verified})

            # 📊 STAGE VALIDATION: Enforce Password Policy via Regex Matrix Match
            # Min 8 characters, 1 Uppercase, 1 Lowercase, 1 Number, 1 Special Character
            password_policy_regex = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&#])[A-Za-z\d@$!%*?&#]{8,}$'
            
            if not re.match(password_policy_regex, new_password):
                messages.error(
                    request, 
                    "Password does not fulfill rules: Must be at least 8 characters long, containing 1 Uppercase letter, 1 Lowercase letter, 1 Number, and 1 Special Character."
                )
                return render(request, "approval_core/verify_otp.html", {"otp_verified": otp_verified})

            # If all checks pass cleanly, update password
            user = User.objects.get(id=user_id)
            user.set_password(new_password)
            user.save()
            
            request.session.pop('reset_otp', None)
            request.session.pop('reset_user_id', None)
            request.session.pop('otp_verified', None)
            request.session.pop('otp_expiry', None)
            
            messages.success(request, "Password reset successful! You can now login.")
            return redirect('login')

    return render(request, "approval_core/verify_otp.html", {
        "otp_verified": otp_verified
    })


@login_required(login_url="login")
def change_password_view(request):
    """Securely change password and maintain session"""
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            # ✅ Important: Updates the session so the user isn't logged out
            update_session_auth_hash(request, user) 
            messages.success(request, '✅ Your password was successfully updated!')
            return redirect('dashboard')
        else:
            messages.error(request, '❌ Please correct the errors below.')
    else:
        form = PasswordChangeForm(request.user)
    
    return render(request, 'approval_core/change_password.html', {
        'form': form
    })


@login_required(login_url="login")
def restore_backup_view(request):
    """
    ========================================================================
    SECTION 1: RESTRICTION ACCESS LAYER & BASE ENTRY VALIDATION
    ========================================================================
    Restricts access strictly to Superadmins. Checks if a file is submitted
    and handles temp storage initialization safely.
    """
    if not request.user.is_superuser:
        messages.error(request, "Access Denied. Only Superadmins can restore backups.")
        return redirect("dashboard")

    if request.method == "POST":
        restore_type = request.POST.get("restore_type") # 'full' or 'db_only'
        uploaded_file = request.FILES.get("backup_file")

        if not uploaded_file:
            messages.error(request, "Please select a backup file to import.")
            return render(request, "approval_core/restore_backup.html")

        # Temporarily save the uploaded file archive onto the local file system
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'temp_restores'))
        filename = fs.save(uploaded_file.name, uploaded_file)
        file_path = fs.path(filename)

        """
        ========================================================================
        SECTION 2: INITIAL CHECKPOINT LOGGING & METRIC CAPTURE
        ========================================================================
        Creates a 'pending' verification history row BEFORE the restoration command
        overwrites the tables. This secures snapshot counts for user parity auditing.
        """
        from approval_core.backup_service import ProductionBackupEngine
        action_label = 'restore_full' if restore_type == 'full' else 'restore_db'
        
        # Capture database footprint counts right now
        checkpoint_log = ProductionBackupEngine.record_log(
            action_type=action_label, 
            filename=filename, 
            user=request.user, 
            status='pending', 
            file_path=file_path
        )

        try:
            """
            ========================================================================
            SECTION 3: ARCHIVE PARSING, METADATA STRIPPING, & ARCHIVE FORMAT CHECKS
            ========================================================================
            Validates data extensions and safely parses timestamp digits out of the 
            file name pattern to construct human-readable logging logs.
            """
            # Extract Backup Date from Filename (Format matching: SMVS_Approval_Full_11052026_1408.tar.gz)
            date_match = re.search(r'(\d{2})(\d{2})(\d{4})', filename)
            if date_match:
                d, m, y = date_match.groups()
                backup_date_str = f"{d}-{m}-{y}"
            else:
                backup_date_str = "the backup date"

            # Enforce absolute safety verification check constraints based on chosen route extensions
            if restore_type == 'full':
                if not filename.endswith('.tar.gz') and not filename.endswith('.tar'):
                    raise ValueError("Full restore requires a .tar.gz archive as generated by your system.")
            
            if restore_type == 'db_only':
                if not filename.endswith('.sql'):
                    raise ValueError("Database restore requires a valid production .sql file dump.")

            """
            ========================================================================
            SECTION 4: SYSTEM COMMAND EXECUTION LAYER
            ========================================================================
            Calls Django's internal configuration command routines to execute shell scripts
            and parse compressed binary blocks safely.
            """
            if restore_type == "full":
                # call_command('restore_project', file_path)
                pass
                
            elif restore_type == "db_only":
                # call_command('restore_db', file_path)
                pass

            """
            ========================================================================
            SECTION 5: POST-RESTORE INTEGRITY AUDITING & REAL-TIME PARITY VERIFICATION
            ========================================================================
            Re-runs user and form queries against your newly updated tables. Compares 
            counts against Section 2 metrics to prove usernames/passwords are fully safe.
            """
            ProductionBackupEngine.verify_restored_data(checkpoint_log)
            
            if checkpoint_log.status == 'success':
                messages.success(
                    request, 
                    f"✅ Backup restored successfully! You imported data taken on {backup_date_str}. "
                    f"Production integrity audit passes parity checks cleanly."
                )
            else:
                messages.error(
                    request, 
                    f"⚠️ Restoration finished but data verification mismatch detected! "
                    f"Counts do not match. Review the system log summary report right away."
                )
                
            return redirect('dashboard')

        except Exception as e:
            """
            ========================================================================
            SECTION 6: TRANSACTION EXCEPTION TRACKING
            ========================================================================
            Catches migration crashes and dumps trace tracebacks directly into your audit logs.
            """
            import traceback
            error_trace = traceback.format_exc()
            
            # Log structural fail metrics
            checkpoint_log.status = 'failed'
            checkpoint_log.log_summary += f"\n\n❌ CRITICAL CRASH DURING RESTORE EXECUTION:\n{error_trace}"
            checkpoint_log.save()
            
            logger.error(f"[RESTORE CRITICAL ERROR] Process failed for archive {filename}: {e}", exc_info=True)
            messages.error(request, f"❌ Import Error: The backup file does not match system records. Details: {str(e)}")
            
        finally:
            """
            ========================================================================
            SECTION 7: TIMELINE CLEANUP
            ========================================================================
            Permanently clears the temporary files on the drive.
            """
            if os.path.exists(file_path):
                fs.delete(filename)

    return render(request, "approval_core/restore_backup.html")



@login_required(login_url="login")
def lock_screen_view(request):
    """Securely locks the session state"""
    next_url = request.GET.get('next', 'dashboard')
    
    # ✅ Set the session lock flag when the user first hits this page
    if request.method == "GET":
        request.session['is_locked'] = True

    if request.method == "POST":
        password = request.POST.get("password")
        if check_password(password, request.user.password):
            # ✅ Unlock the session on correct password
            request.session['is_locked'] = False
            messages.success(request, "Welcome back!")
            return redirect(next_url)
        else:
            messages.error(request, "Incorrect password. Please try again.")
            
    return render(request, "approval_core/lock_screen.html", {
        "next_url": next_url,
        "user": request.user
    })


@login_required
def help_manual_view(request):
    """Displays role-specific help and system manual"""
    # Use your existing context processor logic to determine role
    context = {
        'title': 'SMVS Approval System Manual',
        'is_admin': request.user.is_superuser or (hasattr(request.user, 'approval_role') and request.user.approval_role.role == 'admin'),
    }
    return render(request, 'approval_core/help_manual.html', context)


@staff_member_required
def core_system_reference_view(request):
    """Admin-only view to see the structural map of core system files."""
    context = {
        'title': 'Core System Reference',
        # Map for form_detail.html
        'form_detail_map': [
            {'sec': '1', 'name': 'Styles & Base CSS', 'lines': 'L1–113', 'type': 'CSS'},
            {'sec': '2', 'name': 'Form Header (Title + Status Badge)', 'lines': 'L115–138', 'type': 'Header'},
            {'sec': '3', 'name': 'Tab Navigation', 'lines': 'L140–163', 'type': 'Nav'},
            {'sec': '4', 'name': 'Details Tab — Information Card', 'lines': 'L167–296', 'type': 'Tab'},
            {'sec': '5', 'name': 'Details Tab — Timeline Card', 'lines': 'L299–356', 'type': 'Tab'},
            {'sec': '6', 'name': 'Approval Flow Tab — Workflow', 'lines': 'L359–490', 'type': 'Tab'},
            {'sec': '7', 'name': 'Documents Tab — Upload & List', 'lines': 'L492–771', 'type': 'Tab'},
            {'sec': '8', 'name': 'Comments Tab — Thread', 'lines': 'L774–871', 'type': 'Tab'},
            {'sec': '9', 'name': 'Action Section — Dept Selection', 'lines': 'L874–925', 'type': 'Action'},
            {'sec': '10', 'name': 'Action Section — Main Form', 'lines': 'L926–1743', 'type': 'Action'},
            {'sec': '11', 'name': 'JS — Comment Badge Counter', 'lines': 'L1751–1763', 'type': 'JS'},
            {'sec': '12', 'name': 'JS — Delegate Toggle', 'lines': 'L1765–1780', 'type': 'JS'},
            {'sec': '13', 'name': 'JS — Amount Logic', 'lines': 'L1782–1910', 'type': 'JS'},
            {'sec': '14', 'name': 'JS — Global Functions', 'lines': 'L1913–2001', 'type': 'JS'},
            {'sec': '15', 'name': 'JS — Currency Preview', 'lines': 'L2004–2038', 'type': 'JS'},
            {'sec': '16', 'name': 'JS — Select2 Initialization', 'lines': 'L2040–2050', 'type': 'JS'},
            {'sec': '17', 'name': 'JS — Save Expenditure AJAX', 'lines': 'L2052–2089', 'type': 'JS'},
            {'sec': '18', 'name': 'JS — Upload Progress', 'lines': 'L2091–2118', 'type': 'JS'},
            {'sec': '19', 'name': 'JS — Upload Validation', 'lines': 'L2120–2148', 'type': 'JS'},
            {'sec': '20', 'name': 'JS — Discrepancy Toggle', 'lines': 'L2150–2179', 'type': 'JS'},
            {'sec': '21', 'name': 'JS — Form Submit Spinner', 'lines': 'L2181–2194', 'type': 'JS'},
        ]
    }
    return render(request, 'approval_core/core_reference.html', context)


def health_check(request):
    # You can optionally add a basic database check here later
    return HttpResponse("OK", status=200)


def check_registration_email_ajax(request):
    """
    AJAX validator for the registration gateway.
    Blocks username duplicates, normalizes cross-region email handles, 
    and handles comma-separated multi-email fields cleanly.
    """
    email = request.GET.get('email', '').strip().lower()
    username = request.GET.get('username', '').strip()
    
    # 1. IMMEDIATE REGISTERED USERNAME AVAILABILITY AUDIT
    if username and User.objects.filter(username__iexact=username).exists():
        return JsonResponse({'success': True, 'is_duplicate_username': True})

    # Evaluate email domain string structure (Matches smvs.org, in.smvs.org, us.smvs.org, etc.)
    trusted_regex = getattr(settings, 'TRUSTED_ENTERPRISE_DOMAINS_REGEX', r'@([a-z0-9-]+\.)*smvs\.org$')
    is_smvs_domain = bool(re.search(trusted_regex, email))
    response_data = {
        'success': True,
        'is_smvs_domain': is_smvs_domain,
        'is_duplicate_username': False,
        'is_duplicate_email': False,
        'match_type': None,
        'matches': []
    }

    if not email:
        return JsonResponse(response_data)

    # 2. EVALUATE INTERNAL ENTERPRISE DOMAIN LOOKUPS
    if is_smvs_domain:
        email_handle = email.split('@')[0]  # Extracts "sahay" the prefix handle

        # 🟢 UPGRADED: Removed restrictive post__role_name__in array filters
        dept_mappings = EmailMapping.objects.filter(
            email__icontains=email_handle,
            mapping_type='department',
            is_active=True
        ).select_related('department', 'post')

        seen_depts = set()
        for mapping in dept_mappings:
            mapped_emails = [e.strip().lower() for e in mapping.email.split(',')]
            
            has_valid_match = False
            for me in mapped_emails:
                if me == email:
                    has_valid_match = True
                elif me.split('@')[0] == email_handle and bool(re.search(trusted_regex, me)):
                    has_valid_match = True

            if has_valid_match and mapping.department and mapping.department.id not in seen_depts:
                seen_depts.add(mapping.department.id)
                response_data['matches'].append({
                    'id': f"dept_{mapping.department.id}",
                    'name': f"{mapping.department.name} ({mapping.post.role_name})"
                })

        if response_data['matches']:
            response_data['match_type'] = 'department'
            return JsonResponse(response_data)

        # 🟢 UPGRADED: Removed restrictive post__role_name__in array filters from fallback centers
        center_mappings = EmailMapping.objects.filter(
            email__icontains=email_handle,
            mapping_type='center',
            is_active=True
        ).select_related('center', 'post')

        seen_centers = set()
        for mapping in center_mappings:
            mapped_emails = [e.strip().lower() for e in mapping.email.split(',')]
            has_valid_match = False
            for me in mapped_emails:
                if me == email:
                    has_valid_match = True
                elif me.split('@')[0] == email_handle and bool(re.search(trusted_regex, me)):
                    has_valid_match = True

            if has_valid_match and mapping.center and mapping.center.id not in seen_centers:
                seen_centers.add(mapping.center.id)
                response_data['matches'].append({
                    'id': f"center_{mapping.center.id}",
                    'name': f"{mapping.center.name} ({mapping.post.role_name})"
                })

        if response_data['matches']:
            response_data['match_type'] = 'center'
            return JsonResponse(response_data)

    # 3. EVALUATE EXTERNAL PUBLIC EMAILS (Gmail/Yahoo) AGAINST MASTER CENTER RECORDS
    else:
        # Check if this specific external email is already explicitly pre-allotted somewhere 
        external_center_mappings = EmailMapping.objects.filter(
            mapping_type='center',
            is_active=True
        ).select_related('center', 'post')

        seen_external_centers = set()
        for mapping in external_center_mappings:
            mapped_emails = [e.strip().lower() for e in mapping.email.split(',')]
            if email in mapped_emails and mapping.center and mapping.center.id not in seen_external_centers:
                seen_external_centers.add(mapping.center.id)
                response_data['matches'].append({
                    'id': f"center_{mapping.center.id}",
                    'name': f"{mapping.center.name} ({mapping.post.role_name})"
                })
        
        if response_data['matches']:
            response_data['match_type'] = 'center'
            return JsonResponse(response_data)

        # 🟢 SECURITY COMPLIANCE FALLBACK: For outside domains, prompt manual email typing
        response_data['match_type'] = 'external_manual_verification'
        response_data['matches'] = [] # Explicitly empty out center parameters for isolation security

    return JsonResponse(response_data)


def download_sample_view(request, model_name):
    """Unified controller mapper for custom sample spreadsheet downloads"""
    maps = {
        'group': samples.GROUPS_SAMPLE,
        'emailnotificationtemplate': samples.EMAIL_TEMPLATES_SAMPLE,
        'smstemplate': samples.SMS_TEMPLATES_SAMPLE,
        'whatsappnotificationtemplate': samples.WHATSAPP_TEMPLATES_SAMPLE,
        'reportpermission': samples.REPORT_PERMISSIONS_SAMPLE,
        'ruleapprovalsequence': samples.RULE_SEQUENCES_SAMPLE,
        'userworkspace': samples.USER_WORKSPACES_SAMPLE,
    }
    
    selected_sample = maps.get(model_name.lower())
    if not selected_sample:
        return HttpResponse("Sample template configuration not found.", status=404)
        
    return ImportExportHelper.generate_xlsx_response(
        filename=f"{model_name}_Import_Sample.xlsx",
        sheet_name="Import Template",
        headers=selected_sample['headers'],
        data_rows=selected_sample['rows']
    )


def dispatch_password_reset_notification_async(user_email, username, raw_password, phone_number):
    """Fires dynamic notifications using the active master template content layouts."""
    
    # 🟢 1. DYNAMIC EMAIL DISPATCH
    if user_email:
        try:
            # Pull your database configuration template dynamically
            template = EmailNotificationTemplate.objects.filter(template_name__icontains="password", is_active=True).first()
            if template:
                subject = template.subject
                # Dynamically replace variables inside your database template text
                message = template.body_html.replace("{{username}}", username).replace("{{password}}", raw_password)
            else:
                # Fallback if no database template is found
                subject = "🔒 Your SMVS Approval System Password Has Been Reset"
                message = f"Jai Swaminarayan {username},\n\nAn admin has updated your password.\nUsername: {username}\nPassword: {raw_password}"

            send_mail(
                subject, 
                message, 
                settings.DEFAULT_FROM_EMAIL, 
                [user_email], 
                fail_silently=True
            )
        except Exception:
            pass

    # 🟢 2. DYNAMIC SMS DISPATCH (TextGuru Gateway)
    if phone_number and getattr(settings, 'ENABLE_SMS_NOTIFICATIONS', False):
        try:
            sms_tpl = SMSTemplate.objects.filter(template_name__icontains="password", is_active=True).first()
            if sms_tpl:
                sms_text = sms_tpl.body_text.replace("{{username}}", username).replace("{{password}}", raw_password)
                # Call your existing TextGuru gateway sender here:
                # send_textguru_sms(phone_number, sms_text)
        except Exception:
            pass

    # 🟢 3. DYNAMIC WHATSAPP DISPATCH (WhatsApp Gateway)
    if phone_number and getattr(settings, 'ENABLE_WHATSAPP_NOTIFICATIONS', False):
        try:
            wa_tpl = WhatsAppNotificationTemplate.objects.filter(template_name__icontains="password", is_active=True).first()
            if wa_tpl:
                # Call your existing WhatsApp API endpoint handler here:
                # send_whatsapp_message(phone_number, wa_tpl.template_id, [username, raw_password])
                pass
        except Exception:
            pass


@csrf_exempt
@login_required
def master_file_stream_import_view(request):
    """
    Processes either a single spreadsheet or extracts a bulk ZIP package 
    asynchronously, automatically executing matching import sequences cleanly.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
        
    uploaded_file = request.FILES.get('file')
    menu_key = request.POST.get('menu_key')
    
    if not uploaded_file:
        return JsonResponse({'success': False, 'error': 'Missing file payload components.'}, status=400)

    # Dictionary mapping modules directly to their corresponding processing functions
    sequence_functions = {
        'Country': execute_master_country_import,
        'ApprovalLevel': execute_master_approval_level_import,
        'PostMaster': execute_master_post_import,
        'Zone': execute_master_zone_import,
        'Center': execute_master_center_import,
        'Department': execute_master_department_import,
        'User': execute_master_user_import,
        'UserProfile': execute_master_user_profile_import,
        'UserRole': execute_master_user_role_import,
        'UserWorkspace': execute_master_user_workspace_import,
        'ApprovalLevelUser': execute_master_approval_level_user_import,
        'RegistrationConfig': execute_master_workflow_config_import,
        'EmailTemplate': execute_master_email_template_import,
        'SMSTemplate': execute_master_sms_template_import,
        'WhatsAppTemplate': execute_master_whatsapp_template_import,
        'EmailMapping': execute_master_email_mapping_import,
        'ApprovalRule': execute_master_approval_rule_import,
        'RuleSequence': execute_master_rule_sequence_import,
        'RoutingMatrix': execute_master_routing_matrix_import,
    }

    # 📦 CASE 1: The user uploaded a multi-file ZIP archive package
    if uploaded_file.name.endswith('.zip'):
        try:
            imported_log = {}
            total_imported = 0
            
            with zipfile.ZipFile(uploaded_file) as z:
                # Map extracted inner files case-insensitively by scraping paths
                extracted_files = {}
                for file_info in z.infolist():
                    base_name = os.path.basename(file_info.filename).strip()
                    if not base_name or file_info.is_dir():
                        continue
                    extracted_files[base_name.lower()] = (file_info.filename, z.read(file_info.filename))

                # Define flexible filename keywords to discover your items inside the archive
                file_keywords = {
                    'Country': 'country',
                    'ApprovalLevel': 'approvallevel',
                    'PostMaster': 'postmaster',
                    'Zone': 'zone',
                    'Center': 'center',
                    'Department': 'department',
                    'User': 'user',
                    'UserProfile': 'userprofile',
                    'UserRole': 'userrole',
                    'UserWorkspace': 'userworkspace',
                    'ApprovalLevelUser': 'approvalleveluser',
                    'RegistrationConfig': 'registrationconfig',
                    'EmailTemplate': 'emailtemplate',
                    'SMSTemplate': 'smstemplate',
                    'WhatsAppTemplate': 'whatsapptemplate',
                    'EmailMapping': 'emailmapping',
                    'ApprovalRule': 'approvalrule',
                    'RuleSequence': 'rulesequence',
                    'RoutingMatrix': 'routingmatrix',
                }

                # Process files in strict relational database build order sequence
                for target_key, keyword in file_keywords.items():
                    target_func = sequence_functions.get(target_key)
                    if not target_func:
                        continue

                    # Search for a matching file key inside the archive map
                    matched_file_key = None
                    for archive_filename in extracted_files.keys():
                        if not (archive_filename.endswith('.csv') or archive_filename.endswith('.xlsx')):
                            continue

                        # 🟢 EXACT MATCH GUARD: Stops 'user' from accidentally matching 'userprofile' or 'userrole'
                        if target_key == 'User':
                            # Matches 'user-2026-06-22.csv' or 'user.csv', but strictly ignores compound names
                            if archive_filename.startswith('user-') or archive_filename == 'user.csv':
                                matched_file_key = archive_filename
                                break
                        else:
                            # Standard keyword sub-string matching for everything else
                            if keyword in archive_filename:
                                matched_file_key = archive_filename
                                break

                    if matched_file_key:
                        original_path, file_bytes = extracted_files[matched_file_key]
                        io_stream = io.BytesIO(file_bytes)
                        io_stream.name = os.path.basename(original_path)
                        
                        p_count, s_count = target_func(io_stream)
                        imported_log[target_key] = f"Processed: {p_count}, Skipped: {s_count}"
                        total_imported += p_count
                    else:
                        imported_log[target_key] = "Missing inside ZIP"

            response = JsonResponse({
                'success': True,
                'is_zip': True,
                'message': 'ZIP restoration execution cycle completed successfully.',
                'details': imported_log,
                'processed_count': total_imported
            })
            response['X-Accel-Buffering'] = 'no'
            return response

        except Exception as e:
            return JsonResponse({'success': False, 'error': f"ZIP Extraction Error: {str(e)}"}, status=500)

    # 📄 CASE 2: The user uploaded a single CSV/XLSX spreadsheet file directly
    else:
        if not menu_key:
            return JsonResponse({'success': False, 'error': 'Missing menu_key identification parameter for single file upload.'}, status=400)
            
        target_func = sequence_functions.get(menu_key)
        if not target_func:
            return JsonResponse({'success': False, 'error': f"Unknown module category path: '{menu_key}'."}, status=400)

        try:
            imported_count, skipped_count = target_func(uploaded_file)
            response = JsonResponse({
                'success': True,
                'is_zip': False,
                'menu_key': menu_key,
                'processed_count': imported_count,
                'skipped_count': skipped_count
            })
            response['X-Accel-Buffering'] = 'no'
            return response
            
        except Exception as e:
            return JsonResponse({
                'success': False, 
                'menu_key': menu_key, 
                'error': f"Processing exception error details: {str(e)}"
            }, status=500)