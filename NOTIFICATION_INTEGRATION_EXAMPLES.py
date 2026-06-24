"""
Example: Integration of SMS/WhatsApp notifications into your approval workflow

Copy these examples into your approval_workflow/views.py or approval_core/views.py
"""

# ============================================
# EXAMPLE 1: Notify when form reaches approval level
# ============================================

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from approval_core.models import ApprovalForm, ApprovalLevel
from approval_core.notification_service import ApprovalLevelNotificationService
import logging

logger = logging.getLogger(__name__)

@login_required
def move_to_next_approval_level(request, form_id):
    """
    Move form to next approval level and notify the approvers
    """
    form = get_object_or_404(ApprovalForm, id=form_id)
    
    # Get the next approval level (example: level 2 - MK Sabhya)
    next_level = ApprovalLevel.objects.get(level_number=2)
    
    # Update form status
    form.status = 'pending_mk_sabhya'
    form.current_approval_level = next_level
    form.save()
    
    # Send notifications
    notification_service = ApprovalLevelNotificationService()
    results = notification_service.notify_approval_pending(
        approval_form=form,
        approval_level=next_level,
        approver_user=request.user,
        context={
            'remarks': 'Form moved to next approval level',
            'action': 'review'
        }
    )
    
    # Log results
    for result in results:
        if result['success']:
            logger.info(f"Notification sent via {result['channel']} to {result['user']}")
        else:
            logger.error(f"Failed to send {result['channel']} to {result['user']}: {result['msg_id']}")
    
    return JsonResponse({
        'status': 'success',
        'message': 'Form moved and notifications sent',
        'notifications_sent': len([r for r in results if r['success']])
    })


# ============================================
# EXAMPLE 2: Approve form and notify submitter
# ============================================

@login_required
def approve_form(request, form_id):
    """
    Approve a form and notify the form submitter
    """
    form = get_object_or_404(ApprovalForm, id=form_id)
    approval_level = form.current_approval_level
    
    # Your approval logic here
    form.status = 'approved'
    form.approved_at = timezone.now()
    form.save()
    
    # Notify form submitter
    notification_service = ApprovalLevelNotificationService()
    notification_service.notify_approval_completed(
        approval_form=form,
        approval_level=approval_level,
        action='approved',
        approver_user=request.user,
        context={
            'remarks': 'Form approved successfully',
            'approval_date': form.approved_at.strftime('%d-%m-%Y')
        }
    )
    
    return JsonResponse({'status': 'success', 'message': 'Form approved'})


# ============================================
# EXAMPLE 3: Reject form and notify submitter
# ============================================

@login_required
def reject_form(request, form_id):
    """
    Reject a form with remarks and notify submitter
    """
    form = get_object_or_404(ApprovalForm, id=form_id)
    approval_level = form.current_approval_level
    rejection_remarks = request.POST.get('remarks', '')
    
    # Update form status
    form.status = 'rejected'
    form.rejected_at = timezone.now()
    form.save()
    
    # Notify form submitter with rejection remarks
    notification_service = ApprovalLevelNotificationService()
    notification_service.notify_approval_completed(
        approval_form=form,
        approval_level=approval_level,
        action='rejected',
        approver_user=request.user,
        context={
            'remarks': rejection_remarks,
            'reason': 'Rejected at ' + approval_level.level_name
        }
    )
    
    return JsonResponse({'status': 'success', 'message': 'Form rejected'})


# ============================================
# EXAMPLE 4: Delegate form and notify new approver
# ============================================

@login_required
def delegate_form(request, form_id):
    """
    Delegate form to another approver
    """
    form = get_object_or_404(ApprovalForm, id=form_id)
    approval_level = form.current_approval_level
    delegate_user_id = request.POST.get('delegate_to')
    delegate_user = get_object_or_404(User, id=delegate_user_id)
    
    # Update form
    form.is_delegated = True
    form.delegated_by = request.user
    form.delegated_to = delegate_user
    form.save()
    
    # Notify new approver
    notification_service = ApprovalLevelNotificationService()
    
    # Send direct notification to the delegate
    message = f"""
Form {form.form_number} has been delegated to you for approval.
Subject: {form.subject}
Amount: {form.amount}
Delegated by: {request.user.get_full_name()}
"""
    
    if delegate_user.email:
        notification_service.notification_service.send_email(
            delegate_user.email,
            f"Delegated Form: {form.form_number}",
            message
        )
    
    # Send SMS if phone available
    from approval_core.sms_whatsapp_models import ApprovalLevelContact
    contact = ApprovalLevelContact.objects.filter(
        user=delegate_user,
        approval_level=approval_level
    ).first()
    
    if contact and contact.phone_number:
        notification_service.notification_service.send_sms(
            contact.phone_number,
            f"Form {form.form_number} delegated to you. Check system."
        )
    
    return JsonResponse({'status': 'success', 'message': 'Form delegated'})


# ============================================
# EXAMPLE 5: Request revision and notify submitter
# ============================================

@login_required
def request_revision(request, form_id):
    """
    Request revision on form and notify submitter
    """
    form = get_object_or_404(ApprovalForm, id=form_id)
    approval_level = form.current_approval_level
    revision_comments = request.POST.get('comments', '')
    
    # Update form status
    form.status = 'revision_pending'
    form.save()
    
    # Notify form submitter
    notification_service = ApprovalLevelNotificationService()
    
    subject = f"Revision Required: Form {form.form_number}"
    message = f"""
Your form requires revision:

Form Number: {form.form_number}
Subject: {form.subject}
Requested by: {request.user.get_full_name()}

Revision Comments:
{revision_comments}

Please log in and make the requested changes.
"""
    
    if form.submitted_by and form.submitted_by.email:
        notification_service.notification_service.send_email(
            form.submitted_by.email,
            subject,
            message
        )
    
    # Also send SMS if phone available
    if hasattr(form.submitted_by, 'user_profile') and form.submitted_by.user_profile.phone:
        notification_service.notification_service.send_sms(
            form.submitted_by.user_profile.phone,
            f"Form {form.form_number} needs revision. Check email for details."
        )
    
    return JsonResponse({'status': 'success', 'message': 'Revision requested'})


# ============================================
# EXAMPLE 6: Test notification (for admin)
# ============================================

@login_required
def test_notification(request):
    """
    Test notification channels (for admin/testing only)
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Admin only'}, status=403)
    
    test_phone = request.POST.get('phone', '+919876543210')
    test_email = request.POST.get('email', request.user.email)
    
    notification_service = ApprovalLevelNotificationService()
    results = {}
    
    # Test Email
    success, msg_id = notification_service.notification_service.send_email(
        test_email,
        'Test Email from SMVS',
        'This is a test email. If you received it, email is working!'
    )
    results['email'] = {'success': success, 'message_id': msg_id}
    
    # Test SMS
    success, msg_id = notification_service.notification_service.send_sms(
        test_phone,
        'Test SMS from SMVS Approval System. If received, SMS is working!'
    )
    results['sms'] = {'success': success, 'message_id': msg_id}
    
    # Test WhatsApp
    success, msg_id = notification_service.notification_service.send_whatsapp(
        test_phone,
        'Test WhatsApp from SMVS. If received, WhatsApp is working!'
    )
    results['whatsapp'] = {'success': success, 'message_id': msg_id}
    
    return JsonResponse(results)


# ============================================
# IMPORTANT: Add to your URL patterns
# ============================================

"""
Add these URLs to your approval_workflow/urls.py or approval_core/urls.py:

path('api/move-to-level/<int:form_id>/', move_to_next_approval_level, name='move_to_level'),
path('api/approve/<int:form_id>/', approve_form, name='approve_form'),
path('api/reject/<int:form_id>/', reject_form, name='reject_form'),
path('api/delegate/<int:form_id>/', delegate_form, name='delegate_form'),
path('api/request-revision/<int:form_id>/', request_revision, name='request_revision'),
path('api/test-notification/', test_notification, name='test_notification'),
"""
