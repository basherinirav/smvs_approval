"""
SMS and WhatsApp notification service
Service layer for sending notifications via multiple channels with integrated log tracking
"""
import logging
import requests
import json
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User

# Core imports aligned with database schema tracking models
from approval_core.models import (
    WhatsAppNotificationTemplate, 
    SMSTemplate, 
    NotificationLog
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Unified notification service for Email, SMS (TextGuru), and WhatsApp (Pinbot.ai)"""

    def __init__(self):
        self.enable_sms = getattr(settings, 'ENABLE_SMS_NOTIFICATIONS', False)
        
        # TextGuru credentials
        self.textguru_loginid = getattr(settings, 'TEXTGURU_LOGINID', None)
        self.textguru_password = getattr(settings, 'TEXTGURU_PASSWORD', None)
        self.textguru_senderid = getattr(settings, 'TEXTGURU_SENDERID', None)
        self.textguru_api_url = getattr(settings, 'TEXTGURU_API_URL', 'https://www.txtguru.in/imobile/api.php')
        
        # Default DLT Template
        self.default_template_id = '1507167109459527075'

        # Pinbot.ai WhatsApp credentials
        self.whatsapp_api_key = getattr(settings, 'WHATSAPP_API_KEY', None)
        self.whatsapp_from_number = getattr(settings, 'WHATSAPP_FROM_NUMBER', None)
        self.whatsapp_base_url = getattr(settings, 'WHATSAPP_BASE_URL', 'https://partnersv1.pinbot.ai/v3/723335100870260/messages')

        logger.info("NotificationService initialized with Pinbot.ai WhatsApp endpoint integration.")

    def send_sms(self, phone_number, message, template_id=None, form_instance=None, event_type="submission"):
        """Send SMS via TextGuru.in API with automatic NotificationLog tracking"""
        if not self.enable_sms:
            logger.warning("SMS notifications are disabled in settings")
            return False, "SMS notifications disabled"

        if not self.textguru_loginid or not self.textguru_password or not self.textguru_senderid:
            logger.error("TextGuru credentials (LOGINID/PASSWORD/SENDERID) are missing in settings")
            return False, "TextGuru credentials missing"

        phone = phone_number.strip()
        if phone.startswith('+91'):
            phone = phone[1:]
        elif not phone.startswith('91'):
            phone = '91' + phone.lstrip('0')

        if template_id is None:
            template_id = self.default_template_id

        params = {
            'username': self.textguru_loginid,
            'password': self.textguru_password,
            'source': self.textguru_senderid,
            'dmobile': phone,
            'message': message.strip(),
            'dlttempid': template_id,
        }

        try:
            logger.info(f"Attempting to send SMS to {phone} | TemplateID: {template_id}")
            response = requests.get(self.textguru_api_url, params=params, timeout=25)
            response_text = response.text.strip()
            success = response.status_code == 200 and "MsgID" in response_text

            # 🟢 LOG TRANSLATION STEP FOR CELLULAR SMS ROUTE
            if form_instance:
                NotificationLog.objects.create(
                    form=form_instance,
                    notification_type=event_type,
                    media_channel="sms",
                    status="sent" if success else "failed",
                    recipient_phone=phone_number,
                    message=message.strip(),
                    is_sent=success,
                    sent_at=timezone.now() if success else None,
                    error_message=None if success else f"HTTP {response.status_code}: {response_text}"
                )

            if success:
                logger.info(f"✅ SMS sent successfully to {phone}")
                return True, response_text
            else:
                return False, response_text

        except Exception as e:
            logger.error(f"❌ Unexpected error while sending SMS: {str(e)}", exc_info=True)
            if form_instance:
                NotificationLog.objects.create(
                    form=form_instance,
                    notification_type=event_type,
                    media_channel="sms",
                    status="failed",
                    recipient_phone=phone_number,
                    message=message.strip(),
                    is_sent=False,
                    error_message=str(e)
                )
            return False, str(e)

    def send_email(self, recipient_email_string, subject, message, form_instance=None, event_type="approval"):
        """Send email notification with dynamic multi-recipient HTML tracking logs"""
        try:
            email_list = [email.strip() for email in recipient_email_string.split(',') if email.strip()]
            
            if not email_list:
                logger.warning("Empty recipient email list provided.")
                return False, "No valid email addresses"

            send_mail(
                subject=subject,
                message="Please use an HTML compatible email client.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=email_list,
                fail_silently=False,
                html_message=message,
            )
            
            # 🟢 LOG TRANSLATION STEP FOR EMAIL MESSAGE TRAIL
            if form_instance:
                NotificationLog.objects.create(
                    form=form_instance,
                    notification_type=event_type,
                    media_channel="email",
                    status="sent",
                    recipient_email=recipient_email_string,
                    subject=subject,
                    message=message,
                    is_sent=True,
                    sent_at=timezone.now()
                )

            logger.info(f"✅ Email blast sent successfully to: {', '.join(email_list)}")
            return True, None
        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email_string}: {str(e)}")
            if form_instance:
                NotificationLog.objects.create(
                    form=form_instance,
                    notification_type=event_type,
                    media_channel="email",
                    status="failed",
                    recipient_email=recipient_email_string,
                    subject=subject,
                    message=message,
                    is_sent=False,
                    error_message=str(e)
                )
            return False, str(e)

    def send_whatsapp(self, phone_number, message):
        """Send raw WhatsApp string payload package straight to Pinbot API"""
        if not str(self.whatsapp_api_key).strip() or not str(self.whatsapp_base_url).strip():
            logger.error("❌ Pinbot credentials missing in configuration.")
            return False, "Pinbot credentials missing"
        
        try:
            clean_phone = phone_number.strip().replace("+", "").lstrip("0")
            if not clean_phone.startswith('91') and len(clean_phone) == 10:
                clean_phone = f"91{clean_phone}"
            
            headers = {
                "ApiKey": self.whatsapp_api_key,
                "Content-Type": "application/json"
            }
            
            payload = {
                "messaging_product": "whatsapp",
                "to": clean_phone,
                "type": "text",
                "text": {"body": message.strip()}
            }
            
            response = requests.post(self.whatsapp_base_url, headers=headers, json=payload, timeout=15)
            response_data = response.text.strip()
            
            if response.status_code in [200, 201, 202]:
                return True, response_data
            return False, response_data
                
        except Exception as e:
            logger.error(f"❌ Exception inside Pinbot raw dispatch: {str(e)}")
            return False, str(e)

    def send_dynamic_whatsapp_by_event(self, event_type, approval_form, recipient_phone, approver_name=None, remarks=None):
        """
        ⚡ MASTER UNIFIED WHATSAPP ROUTER MATRIX
        🟢 UPDATED: Automatically registers fully compiled template message details inside NotificationLog.
        """
        from django.db.models import Q
        from approval_core.models import WhatsAppNotificationTemplate

        template = WhatsAppNotificationTemplate.objects.filter(
            Q(event_type__icontains=event_type) | 
            Q(event_type__icontains='pending_approval' if event_type in ['pending_approval', 'WKF-01'] else '') |
            Q(event_type__icontains='revision_requested' if event_type in ['revision_requested', 'WKF-02'] else '') |
            Q(event_type__icontains='final_approved' if event_type in ['final_approved', 'WKF-03'] else '') |
            Q(event_type__icontains='final_approved_amount_changed' if event_type in ['final_approved_amount_changed', 'WKF-04'] else '') |
            Q(event_type__icontains='amount_changed_notify_approvers' if event_type in ['amount_changed_notify_approvers', 'WKF-05'] else '') |
            Q(event_type__icontains='rejected' if event_type in ['rejected', 'WKF-06'] else '') |
            Q(event_type__icontains='form_submitted' if event_type in ['form_submitted', 'FRM-01'] else '') |
            Q(event_type__icontains='otp_sent' if event_type in ['otp_sent', 'SYS-01'] else '') |
            Q(event_type__icontains='reg_user_activated' if event_type in ['reg_user_activated', 'REG-03'] else '') |
            Q(event_type__icontains='reg_user_declined' if event_type in ['reg_user_declined', 'REG-04'] else '') |
            Q(event_type__icontains='delegated' if event_type in ['delegated', 'DLG-01'] else '') |
            Q(event_type__icontains='external_delegated' if event_type in ['external_delegated', 'DLG-02'] else '') |
            Q(event_type__icontains='approved_by_internal' if event_type in ['approved_by_internal', 'DLG-03'] else '') |
            Q(event_type__icontains='approved_by_external' if event_type in ['approved_by_external', 'DLG-04'] else '') |
            Q(event_type__icontains='rejected_by_internal' if event_type in ['rejected_by_internal', 'DLG-05'] else '') |
            Q(event_type__icontains='rejected_by_external' if event_type in ['rejected_by_external', 'DLG-06'] else '') |
            Q(event_type__icontains='delegation_returned' if event_type in ['delegation_returned', 'DLG-07'] else '') |
            Q(event_type__icontains='external_delegation_reply' if event_type in ['external_delegation_reply', 'DLG-08'] else ''),
            is_active=True
        ).first()

        if not template:
            logger.warning(f"⚠️ No active template for event: {event_type}")
            return False, "Template not configured"
            
        # Parse variable elements
        segment_name = "General"
        form_number = "N/A"
        subject = "Account/Delegation Update"
        amount_str = "N/A"
        approved_amount_str = "N/A"
        base_amount = 0

        if approval_form:
            form_number = approval_form.form_number
            subject = approval_form.subject
            base_amount = approval_form.amount
            
            if approval_form.center:
                segment_name = approval_form.center.name
            elif approval_form.selected_center:
                segment_name = approval_form.selected_center.name
            elif approval_form.department:
                segment_name = approval_form.department.name

            curr_code = getattr(approval_form, 'currency_code', 'INR') or 'INR'
            curr_sym = getattr(approval_form, 'currency_symbol', '₹') or '₹'
            
            if curr_code != 'INR' and approval_form.amount_inr:
                amount_str = f"{curr_sym}{approval_form.amount} (₹{approval_form.amount_inr})"
            else:
                amount_str = f"{curr_sym}{approval_form.amount}"

            if approval_form.approved_amount:
                if curr_code != 'INR' and approval_form.approved_amount_local:
                    approved_amount_str = f"{curr_sym}{approval_form.approved_amount_local} (₹{approval_form.approved_amount})"
                else:
                    approved_amount_str = f"₹{approval_form.approved_amount}"
            else:
                approved_amount_str = remarks if remarks else amount_str

        try:
            formatted_message = template.message_body.format(
                form_number=form_number,
                subject=subject,
                amount=base_amount,
                source_name=segment_name,
                amount_str=amount_str,
                approved_amount_str=approved_amount_str,
                approver_name=approver_name or "Approver",
                remarks=remarks or "No remarks provided"
            )
        except KeyError as e:
            logger.error(f"❌ Placeholder layout mismatch: {str(e)}")
            formatted_message = template.message_body

        # 🚀 Send via Pinbot
        success, response_txt = self.send_whatsapp(recipient_phone, formatted_message)

        # 🟢 LOG TRANSLATION STEP FOR WHATSAPP ALERT STREAM
        if approval_form:
            NotificationLog.objects.create(
                form=approval_form,
                notification_type=event_type,
                media_channel="whatsapp",
                status="sent" if success else "failed",
                recipient_phone=recipient_phone,
                message=formatted_message,  # Stores the fully compiled WhatsApp message text!
                is_sent=success,
                sent_at=timezone.now() if success else None,
                error_message=None if success else response_txt
            )

        return success, response_txt

    def format_message(self, template_text, context):
        """Format message with context variables"""
        try:
            return template_text.format(**context)
        except KeyError as e:
            logger.error(f"Missing variable in template: {str(e)}")
            return template_text

    def send_otp_sms(self, phone_number, otp):
        """Sends a specific security OTP for password reset"""
        message = f"Your SMVS Approval System reset code is: {otp}. Valid for 10 minutes."
        # Note: Passing mock form parameters is skipped for login screen securities
        return self.send_sms(phone_number, message)


# ==============================================================================
# Approval Level Workflows Layer
# ==============================================================================

class ApprovalLevelNotificationService:
    """Service to send notifications at specific approval levels"""
    
    def __init__(self):
        self.notification_service = NotificationService()
        logger.info("ApprovalLevelNotificationService initialized")

    def get_approval_level_contacts(self, approval_level):
        """Get all active contacts for an approval level using ApprovalLevelUser"""
        from approval_core.models import ApprovalLevelUser
        return ApprovalLevelUser.objects.filter(approval_level=approval_level, is_active=True).select_related('user')

    def get_notification_channels(self, approval_level_name):
        """Get configured notification channels for an approval level"""
        config = settings.APPROVAL_LEVEL_NOTIFICATION_CONFIG.get(approval_level_name.lower(), {})
        return config.get('channels', ['email'])

    def get_sms_message(self, approval_form, event_type, approver_name=None):
        """Returns formatted SMS message for different events"""
        today = timezone.now().strftime('%d-%m-%Y')
        current_time = timezone.now().strftime('%H:%M')
        
        if approval_form.amount:
            currency_code = getattr(approval_form, 'currency_code', 'INR') or 'INR'
            currency_symbol = getattr(approval_form, 'currency_symbol', '₹') or '₹'
            if currency_code != 'INR':
                amount_inr = getattr(approval_form, 'amount_inr', None)
                amount_str = f"{currency_symbol}{approval_form.amount} (Rs.{amount_inr})" if amount_inr else f"{currency_symbol}{approval_form.amount}"
            else:
                amount_str = f"Rs.{approval_form.amount}"
        else:
            amount_str = ""
        
        text_map = {
            'submitted': f"Form Submitted - {amount_str}",
            'pending': f"Pending your approval - {amount_str}",
            'approved': f"Approved by {approver_name or 'Approver'} - {amount_str}",
            'final_approved': f"Final Approved - {amount_str}",
            'rejected': f"Rejected by {approver_name or 'Approver'} - {amount_str}",
            'delegated': f"Delegated to new approver - {amount_str}",
            'delegated_return': f"Delegation Returned - {amount_str}"
        }
        text = text_map.get(event_type, f"Update - {amount_str}")

        return f"FM={approval_form.form_number} SUB={approval_form.subject} {text} D={today} T={current_time}-RJPSWM"

    def notify_approval_pending(self, approval_form, approval_level, approver_user=None, context=None):
        """Send text SMS and Pinbot.ai dynamic WhatsApp notifications to ALL active level approvers"""
        if context is None:
            context = {}

        logger.info(f"Starting multi-recipient approval notification for Form: {approval_form.form_number}")
        sms_message = self.get_sms_message(approval_form, 'pending')

        from approval_core.models import ApprovalLevelUser
        approvers = ApprovalLevelUser.objects.filter(approval_level=approval_level, is_active=True).select_related('user', 'user__user_profile')

        if approval_form.department:
            approvers = approvers.filter(departments=approval_form.department)

        if not approvers.exists():
            logger.warning(f"⚠️ No active level users found to notify for sequence: {approval_level.level_name}")
            return []

        results = []
        for alu in approvers:
            user = alu.user
            phone = getattr(user.user_profile, 'phone', None) if hasattr(user, 'user_profile') else None

            if phone:
                logger.info(f"Processing notification broadcast loop for user: {user.username} ({phone})")
                
                # Deliver tracked SMS
                success_sms, _ = self.notification_service.send_sms(phone, sms_message, form_instance=approval_form, event_type="pending_approval")
                
                # Deliver tracked dynamic WhatsApp
                success_wa, _ = self.notification_service.send_dynamic_whatsapp_by_event(
                    event_type='pending_approval', 
                    approval_form=approval_form, 
                    recipient_phone=phone,
                    approver_name=user.get_full_name() or user.username,
                    remarks=context.get('remarks', '')
                )
                
                results.append({
                    'user': user.username,
                    'phone': phone,
                    'sms_success': success_sms,
                    'whatsapp_success': success_wa
                })

        return results

    def notify_approval_completed(self, approval_form, approval_level, action, approver_user=None, context=None):
        """Send text SMS and Pinbot WhatsApp templates to submitters when action completes"""
        if context is None:
            context = {}

        logger.info(f"Starting completed notification for Form: {approval_form.form_number} | Action: {action}")
        approver_name = approver_user.get_full_name() if approver_user else 'Approver'

        event_map = {
            'approved': 'pending_approval',
            'final_approved': 'final_approved',
            'rejected': 'rejected',
            'revision_requested': 'revision_requested'
        }
        event_type = event_map.get(action, 'pending_approval')
        sms_message = self.get_sms_message(approval_form, action, approver_name)

        if approval_form.submitted_by:
            phone = getattr(approval_form.submitted_by.user_profile, 'phone', None) if hasattr(approval_form.submitted_by, 'user_profile') else None

            if phone:
                # 1. Send tracked SMS
                self.notification_service.send_sms(phone, sms_message, form_instance=approval_form, event_type=event_type)
                
                # 2. Send tracked dynamic WhatsApp
                self.notification_service.send_dynamic_whatsapp_by_event(
                    event_type=event_type,
                    approval_form=approval_form,
                    recipient_phone=phone,
                    approver_name=approver_name,
                    remarks=context.get('remarks', approval_form.latest_approval_remark or '')
                )
        return True


def send_notification_for_approval_level(approval_form, approval_level, action=None, approver_user=None, context=None):
    """Convenience tracking handler bridge"""
    service = ApprovalLevelNotificationService()
    if action is None:
        return service.notify_approval_pending(approval_form, approval_level, approver_user, context)
    else:
        return service.notify_approval_completed(approval_form, approval_level, action, approver_user, context)