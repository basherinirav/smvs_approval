"""
TextGuru SMS Service
"""
import requests
import logging
from django.conf import settings
from approval_core.models import SMSTemplate

logger = logging.getLogger(__name__)


class TextGuruSMSService:
    """Handles SMS sending via TextGuru API"""

    def __init__(self):
        self.loginid = settings.TEXTGURU_LOGINID
        self.password = settings.TEXTGURU_PASSWORD
        self.senderid = settings.TEXTGURU_SENDERID
        self.enabled = getattr(settings, 'ENABLE_SMS_NOTIFICATIONS', True)
        self.base_url = "https://www.textguru.in/api/pushsms"

    def send_sms(self, phone_number: str, message: str):
        """Send SMS via TextGuru"""
        if not self.enabled:
            logger.warning("SMS notifications are disabled")
            return False, "SMS disabled"

        if not phone_number or not self.loginid or not self.password:
            logger.error("TextGuru credentials or phone missing")
            return False, "Missing credentials or phone"

        if not phone_number.startswith('+'):
            phone_number = f"+91{phone_number.lstrip('0')}"

        try:
            payload = {
                'loginid': self.loginid,
                'password': self.password,
                'senderid': self.senderid,
                'mobile': phone_number,
                'text': message[:160],
                'route': 'v3',
            }

            response = requests.post(self.base_url, data=payload, timeout=15)

            if response.status_code == 200:
                logger.info(f"SMS sent to {phone_number}")
                return True, response.text
            else:
                logger.error(f"TextGuru failed: {response.text}")
                return False, response.text

        except Exception as e:
            logger.error(f"SMS error: {e}")
            return False, str(e)

    def get_template_message(self, event_type: str, approval_level=None):
        """Get active SMS template for specific event and level"""
        try:
            if approval_level:
                template = SMSTemplate.objects.get(
                    event_type=event_type,
                    approval_level=approval_level,
                    is_active=True
                )
            else:
                template = SMSTemplate.objects.get(
                    event_type=event_type,
                    approval_level__isnull=True,
                    is_active=True
                )
            return template.message_text
        except SMSTemplate.DoesNotExist:
            # Fallback: try without level
            try:
                template = SMSTemplate.objects.get(
                    event_type=event_type,
                    approval_level__isnull=True,
                    is_active=True
                )
                return template.message_text
            except SMSTemplate.DoesNotExist:
                logger.warning(f"No SMS template found for {event_type}")
                return None
        except Exception as e:
            logger.error(f"Template error: {e}")
            return None