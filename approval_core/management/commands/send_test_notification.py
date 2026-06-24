"""
Management command to test SMS and WhatsApp notifications
Usage: python manage.py send_test_notification --phone +919876543210 --channel sms
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from approval_core.notification_service import NotificationService


class Command(BaseCommand):
    help = 'Send test SMS/WhatsApp notifications'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--phone',
            type=str,
            default='+919876543210',
            help='Phone number to send to (with country code)'
        )
        parser.add_argument(
            '--channel',
            type=str,
            choices=['sms', 'whatsapp', 'both'],
            default='sms',
            help='Notification channel'
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Email address to test'
        )
        parser.add_argument(
            '--message',
            type=str,
            default='Test message from SMVS Approval System',
            help='Custom message to send'
        )
    
    def handle(self, *args, **options):
        phone = options['phone']
        channel = options['channel']
        email = options['email']
        message = options['message']
        
        service = NotificationService()
        
        self.stdout.write(self.style.SUCCESS('Starting notification test...'))
        self.stdout.write(f"Phone: {phone}")
        self.stdout.write(f"Channel: {channel}")
        if email:
            self.stdout.write(f"Email: {email}")
        self.stdout.write(f"Message: {message}\n")
        
        # Test SMS
        if channel in ['sms', 'both']:
            self.stdout.write("Testing SMS...")
            success, msg_id = service.send_sms(phone, message)
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ SMS sent successfully!')
                )
                self.stdout.write(f"  Message ID: {msg_id}\n")
            else:
                self.stdout.write(
                    self.style.ERROR(f'✗ SMS failed: {msg_id}\n')
                )
        
        # Test WhatsApp
        if channel in ['whatsapp', 'both']:
            self.stdout.write("Testing WhatsApp...")
            success, msg_id = service.send_whatsapp(phone, message)
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ WhatsApp sent successfully!')
                )
                self.stdout.write(f"  Message ID: {msg_id}\n")
            else:
                self.stdout.write(
                    self.style.ERROR(f'✗ WhatsApp failed: {msg_id}\n')
                )
        
        # Test Email
        if email:
            self.stdout.write("Testing Email...")
            success, msg_id = service.send_email(
                email,
                'Test Email from SMVS',
                f'Test message:\n\n{message}'
            )
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Email sent successfully!\n')
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f'✗ Email failed: {msg_id}\n')
                )
        
        self.stdout.write(
            self.style.SUCCESS('\nTest completed!')
        )
