"""
SMS and WhatsApp notification service
Service layer for sending notifications via multiple channels
"""
import logging
import requests
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


class NotificationService:
    """Unified notification service for Email, SMS (TextGuru), and WhatsApp"""

    def __init__(self):
        self.enable_sms = getattr(settings, 'ENABLE_SMS_NOTIFICATIONS', False)
        
        # TextGuru credentials
        self.textguru_loginid = getattr(settings, 'TEXTGURU_LOGINID', None)
        self.textguru_password = getattr(settings, 'TEXTGURU_PASSWORD', None)
        self.textguru_senderid = getattr(settings, 'TEXTGURU_SENDERID', None)
        self.textguru_api_url = getattr(settings, 'TEXTGURU_API_URL', 'https://www.txtguru.in/imobile/api.php')
        
        # Default DLT Template (Without Attachment - Recommended)
        self.default_template_id = '1507167109459527075'

        logger.info("NotificationService initialized with TextGuru SMS enabled: %s", self.enable_sms)

    def send_sms(self, phone_number, message, template_id=None):
        """Send SMS via TextGuru.in API with detailed logging"""
        if not self.enable_sms:
            logger.warning("SMS notifications are disabled in settings")
            return False, "SMS notifications disabled"

        if not self.textguru_loginid or not self.textguru_password or not self.textguru_senderid:
            logger.error("TextGuru credentials (LOGINID/PASSWORD/SENDERID) are missing in settings")
            return False, "TextGuru credentials missing"

        try:
            # Format phone number
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

            logger.info(f"Attempting to send SMS to {phone} | TemplateID: {template_id} | Message: {message[:100]}...")

            response = requests.get(self.textguru_api_url, params=params, timeout=15)
            response_text = response.text.strip()

            logger.info(f"TextGuru raw response for {phone}: {response_text}")

            if response.status_code == 200 and "MsgID" in response_text:
                logger.info(f"✅ SMS sent successfully to {phone} (MsgID present)")
                return True, response_text
            else:
                logger.error(f"❌ TextGuru rejected SMS to {phone}. Status: {response.status_code} | Response: {response_text}")
                return False, response_text

        except requests.exceptions.Timeout:
            logger.error(f"❌ SMS request to TextGuru timed out for number {phone_number}")
            return False, "Request timeout"
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ Failed to connect to TextGuru API for number {phone_number}")
            return False, "Connection error"
        except Exception as e:
            logger.error(f"❌ Unexpected error while sending SMS to {phone_number}: {str(e)}", exc_info=True)
            return False, str(e)


    def send_email(self, recipient_email, subject, message):
        """Send email notification with HTML support"""
        try:
            send_mail(
                subject=subject,
                message="Please use an HTML compatible email client.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                fail_silently=False,
                html_message=message,
            )
            logger.info(f"Email sent successfully to {recipient_email}")
            return True, None
        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email}: {str(e)}")
            return False, str(e)
    
    def send_whatsapp(self, phone_number, message):
        """Send WhatsApp message via Twilio"""
        if not self.enable_whatsapp:
            logger.warning("WhatsApp notifications are disabled in settings")
            return False, "WhatsApp notifications disabled"
        
        if not self.twilio_client:
            logger.error("Twilio client not initialized. Check TWILIO credentials in .env")
            return False, "Twilio not configured"
        
        try:
            # Ensure phone number has country code
            if not phone_number.startswith('+'):
                phone_number = f'+91{phone_number}'  # Default to India
            
            # WhatsApp format: whatsapp:+1234567890
            whatsapp_number = f'whatsapp:{phone_number}'
            twilio_whatsapp = f'whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}'
            
            whatsapp_message = self.twilio_client.messages.create(
                body=message,
                from_=twilio_whatsapp,
                to=whatsapp_number
            )
            
            logger.info(f"WhatsApp sent successfully to {phone_number} (SID: {whatsapp_message.sid})")
            return True, whatsapp_message.sid
        
        except Exception as e:
            logger.error(f"Failed to send WhatsApp to {phone_number}: {str(e)}")
            return False, str(e)

    def format_message(self, template_text, context):
        """Format message with context variables"""
        try:
            return template_text.format(**context)
        except KeyError as e:
            logger.error(f"Missing variable in template: {str(e)}")
            return template_text

    def send_otp_sms(self, phone_number, otp):
        """Sends a specific security OTP for password reset"""
        # Fetch the template from the database if you added 'otp_sent' to SMSTemplate
        message = f"Your SMVS Approval System reset code is: {otp}. Valid for 10 minutes."
        # Use the existing send_sms method
        return self.send_sms(phone_number, message)

class ApprovalLevelNotificationService:
    """Service to send notifications at specific approval levels"""
    
    def __init__(self):
        self.notification_service = NotificationService()
        logger.info("ApprovalLevelNotificationService initialized")

    def get_approval_level_contacts(self, approval_level):
        """Get all active contacts for an approval level using ApprovalLevelUser"""
        from approval_core.models import ApprovalLevelUser   # ← Changed import

        contacts = ApprovalLevelUser.objects.filter(
            approval_level=approval_level,
            is_active=True
        ).select_related('user')

        # Optional: Filter by department if form has department (you can pass form later if needed)
        return contacts

    def get_notification_channels(self, approval_level_name):
        """Get configured notification channels for an approval level"""
        config = settings.APPROVAL_LEVEL_NOTIFICATION_CONFIG.get(
            approval_level_name.lower(), {}
        )
        return config.get('channels', ['email'])

    def get_sms_message(self, approval_form, event_type, approver_name=None):
        """Returns formatted SMS message for different events"""
        today = timezone.now().strftime('%d-%m-%Y')
        current_time = timezone.now().strftime('%H:%M')
        
        # ✅ Show local currency in SMS if foreign, else INR
        if approval_form.amount:
            currency_code = getattr(approval_form, 'currency_code', 'INR') or 'INR'
            currency_symbol = getattr(approval_form, 'currency_symbol', '₹') or '₹'
            if currency_code != 'INR':
                amount_inr = getattr(approval_form, 'amount_inr', None)
                if amount_inr:
                    amount_str = f"{currency_symbol}{approval_form.amount} (Rs.{amount_inr})"
                else:
                    amount_str = f"{currency_symbol}{approval_form.amount}"
            else:
                amount_str = f"Rs.{approval_form.amount}"
        else:
            amount_str = ""
        
        if event_type == 'submitted':
            text = f"Form Submitted - {amount_str}"
        elif event_type == 'pending':
            text = f"Pending your approval - {amount_str}"
        elif event_type == 'approved':
            text = f"Approved by {approver_name or 'Approver'} - {amount_str}"
        elif event_type == 'final_approved':
            text = f"Final Approved - {amount_str}"
        elif event_type == 'rejected':
            text = f"Rejected by {approver_name or 'Approver'} - {amount_str}"
        elif event_type == 'delegated':
            text = f"Delegated to new approver - {amount_str}"
        elif event_type == 'delegated_return':
            text = f"Delegation Returned - {amount_str}"
        else:
            text = f"Update - {amount_str}"

        sms_message = (
            f"FM={approval_form.form_number} "
            f"SUB={approval_form.subject} {text} "
            f"D={today} T={current_time}-RJPSWM"
        )
        
        logger.debug(f"Generated SMS for event '{event_type}': {sms_message}")
        return sms_message

    def notify_approval_pending(self, approval_form, approval_level, approver_user=None, context=None):
        """Send only SMS notification for pending approval"""
        if context is None:
            context = {}

        logger.info(f"Starting pending approval notification for Form: {approval_form.form_number}")

        sms_message = self.get_sms_message(approval_form, 'pending')

        # Import here to avoid circular import issues
        from approval_core.models import ApprovalLevelUser

        approvers = ApprovalLevelUser.objects.filter(
            approval_level=approval_level,
            is_active=True
        )

        if approval_form.department:
            approvers = approvers.filter(departments=approval_form.department)

        if not approvers.exists():
            logger.warning(f"No active approvers found for approval level: {approval_level.level_name}")
            return []

        results = []
        for alu in approvers:
            user = alu.user
            # Get phone from user_profile (matching your workflows.py style)
            phone = getattr(user.user_profile, 'phone', None) if hasattr(user, 'user_profile') else None

            if phone:
                logger.info(f"Sending pending SMS to {user.username} ({phone})")
                success, msg_id = self.notification_service.send_sms(phone, sms_message)
                results.append({
                    'channel': 'sms',
                    'user': user.username,
                    'success': success,
                    'msg_id': msg_id
                })
            else:
                logger.warning(f"No phone number found for user {user.username}")

        logger.info(f"Pending SMS notification completed for Form {approval_form.form_number}. Sent to {len(results)} user(s)")
        return results

    def notify_approval_completed(self, approval_form, approval_level, action, approver_user=None, context=None):
        """Send only SMS notification when approval is completed"""
        if context is None:
            context = {}

        logger.info(f"Starting completed notification for Form: {approval_form.form_number} | Action: {action}")

        approver_name = approver_user.get_full_name() if approver_user else 'Approver'

        # Map action to SMS event type
        event_map = {
            'approved': 'approved',
            'final_approved': 'final_approved',
            'rejected': 'rejected',
            'delegated': 'delegated',
            'delegated_return': 'delegated_return'
        }
        event_type = event_map.get(action, 'approved')

        sms_message = self.get_sms_message(approval_form, event_type, approver_name)

        # Notify submitter via SMS
        if approval_form.submitted_by:
            try:
                from approval_core.models import ApprovalLevelUser   # Not needed here but kept for consistency

                phone = getattr(approval_form.submitted_by.user_profile, 'phone', None) \
                        if hasattr(approval_form.submitted_by, 'user_profile') else None

                if phone:
                    logger.info(f"Sending {action} SMS to submitter {approval_form.submitted_by.username} ({phone})")
                    success, msg_id = self.notification_service.send_sms(phone, sms_message)
                    
                    if success:
                        logger.info(f"✅ Completed SMS sent successfully to submitter for Form {approval_form.form_number}")
                    else:
                        logger.warning(f"❌ Completed SMS failed: {msg_id}")
                else:
                    logger.warning(f"No phone number found for submitter {approval_form.submitted_by.username}")
            except Exception as e:
                logger.error(f"[SMS ERROR] Failed to send completed SMS to submitter: {str(e)}", exc_info=True)

        logger.info(f"Completed notification finished for Form {approval_form.form_number} | Action: {action}")
        return True


# Convenience function outside the class
def send_notification_for_approval_level(approval_form, approval_level, action=None, approver_user=None, context=None):
    """
    Convenience function to send notification when form reaches an approval level
    
    Usage:
        send_notification_for_approval_level(form, approval_level)                    # Pending
        send_notification_for_approval_level(form, approval_level, action='approved', approver_user=user)  # Completed
    """
    logger.info(f"send_notification_for_approval_level called | Form: {approval_form.form_number} | Action: {action or 'pending'}")
    
    service = ApprovalLevelNotificationService()
    
    if action is None:
        return service.notify_approval_pending(approval_form, approval_level, approver_user, context)
    else:
        return service.notify_approval_completed(approval_form, approval_level, action, approver_user, context)