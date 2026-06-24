"""
Simple email sending utility for testing
Place this in your app and call the send_test_email function
"""

from django.core.mail import EmailMessage
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def send_test_email(to_email='basherinirav@gmail.com'):
    """
    Send a test email
    Usage in Django shell:
        from approval_core.email_utils import send_test_email
        send_test_email('basherinirav@gmail.com')
    """
    try:
        email = EmailMessage(
            subject='Test Email from SMVS Approval System',
            body='''Hello,

This is a test email from your SMVS Approval system.

If you received this, your email configuration is working correctly!

Regards,
SMVS Approval System
''',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        
        result = email.send()
        logger.info(f"Email sent successfully to {to_email}. Result: {result}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def send_approval_notification(user_email, approval_level, action):
    """
    Send approval notification email
    """
    try:
        email = EmailMessage(
            subject=f'Approval Status Update - {approval_level}',
            body=f'''Your application has been {action} by {approval_level}.

Please log in to the SMVS Approval system to view details.

{settings.SITE_URL}

Regards,
SMVS Approval System
''',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user_email],
        )
        
        email.send()
        logger.info(f"Notification sent to {user_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send notification: {str(e)}")
        return False
