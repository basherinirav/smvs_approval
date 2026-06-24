import logging
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.template import Template, Context
from approval_core.models import EmailNotificationTemplate, NotificationLog

logger = logging.getLogger(__name__)


class EmailNotificationService:
    """Dynamic email service using admin-editable templates"""

    @staticmethod
    def get_template(event_type):
        """Get active email template for specific event"""
        try:
            return EmailNotificationTemplate.objects.get(
                event_type=event_type,
                is_active=True
            )
        except EmailNotificationTemplate.DoesNotExist:
            logger.warning(f"Email template not found for event: {event_type}")
            return None

    @staticmethod
    def render_template(template_content, context_dict):
        """Render template with context variables"""
        try:
            template = Template(template_content)
            context = Context(context_dict)
            return template.render(context)
        except Exception as e:
            logger.error(f"Template rendering error: {e}")
            return template_content  # fallback

    @staticmethod
    def _send_email(approval_form, subject, body, recipient_email, notification_type):
        """Internal method to send email and log it"""
        try:
            notification = NotificationLog.objects.create(
                form=approval_form,
                notification_type=notification_type,
                recipient_email=recipient_email,
                subject=subject,
                message=body,
                is_sent=False
            )

            send_mail(
                subject=subject,
                message="Please use an HTML compatible email client.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                fail_silently=False,
                html_message=body,
            )

            notification.is_sent = True
            notification.sent_at = timezone.now()
            notification.save()

            logger.info(f"Email sent to {recipient_email} for form {approval_form.form_number} [{notification_type}]")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email}: {e}")
            try:
                notification.error_message = str(e)
                notification.save()
            except:
                pass
            return False

    # ====================== MAIN NOTIFICATION METHODS ======================

    @staticmethod
    def send_pending_approval_notification(approval_form, recipient_user):
        """Send when form reaches next approval level"""
        template = EmailNotificationService.get_template('pending_approval')
        if not template:
            return False

        context = {
            'form': approval_form,
            'user': recipient_user,
            'login_url': f"{settings.SITE_URL}/form/{approval_form.id}/",
            'approval_link_text': getattr(template, 'approval_link_text', 'Click here to review and approve')
        }

        subject = EmailNotificationService.render_template(template.subject, context)
        body = EmailNotificationService.render_template(template.body, context)

        return EmailNotificationService._send_email(
            approval_form, subject, body, recipient_user.email, 'pending_approval'
        )

    @staticmethod
    def send_final_approval_notification(approval_form, recipient_user):
        """Send when form is finally approved"""
        template = EmailNotificationService.get_template('final_approved')
        if not template:
            return False

        context = {
            'form': approval_form,
            'user': recipient_user,
            'login_url': f"{settings.SITE_URL}/form/{approval_form.id}/"
        }

        subject = EmailNotificationService.render_template(template.subject, context)
        body = EmailNotificationService.render_template(template.body, context)

        return EmailNotificationService._send_email(
            approval_form, subject, body, recipient_user.email, 'final_approval'
        )

    @staticmethod
    def send_delegation_notification(form, delegated_to_user=None, external_email=None, delegating_user=None, reason=""):
        """Unified delegation notification - chooses correct template"""
        if external_email:
            # External delegation
            template = EmailNotificationService.get_template('external_delegated')
            recipient_email = external_email
            context = {
                'form': form,
                'delegating_user': delegating_user,
                'reason': reason,
                'login_url': f"{settings.SITE_URL}/guest/form/{form.guest_token}/" if form.guest_token else ""
            }
        else:
            # Internal delegation
            template = EmailNotificationService.get_template('delegated')
            recipient_email = delegated_to_user.email if delegated_to_user else None
            context = {
                'form': form,
                'delegating_user': delegating_user,
                'reason': reason,
                'login_url': f"{settings.SITE_URL}/form/{form.id}/"
            }

        if not template or not recipient_email:
            logger.warning("No template or recipient for delegation notification")
            return False

        subject = EmailNotificationService.render_template(template.subject, context)
        body = EmailNotificationService.render_template(template.body, context)

        return EmailNotificationService._send_email(
            form, subject, body, recipient_email, 'delegated'
        )

    @staticmethod
    def send_return_from_delegation_notification(form, verifier_user, delegating_user, remarks=""):
        """Send when 3rd party returns the form back to delegator"""
        template = EmailNotificationService.get_template('delegation_returned')
        if not template:
            return False

        context = {
            'form': form,
            'verifier': verifier_user,
            'delegating_user': delegating_user,
            'remarks': remarks,
            'login_url': f"{settings.SITE_URL}/form/{form.id}/"
        }

        subject = EmailNotificationService.render_template(template.subject, context)
        body = EmailNotificationService.render_template(template.body, context)

        return EmailNotificationService._send_email(
            form, subject, body, delegating_user.email, 'delegation_returned'
        )

    @staticmethod
    def send_external_delegation_reply_notification(form, decision, remarks="", delegating_user=None):
        """Send notification when external person replies via guest link"""
        template = EmailNotificationService.get_template('external_delegation_reply')
        if not template or not delegating_user or not delegating_user.email:
            return False

        context = {
            'form': form,
            'decision': decision,
            'remarks': remarks,
            'delegating_user': delegating_user,
            'login_url': f"{settings.SITE_URL}/form/{form.id}/"
        }

        subject = EmailNotificationService.render_template(template.subject, context)
        body = EmailNotificationService.render_template(template.body, context)

        return EmailNotificationService._send_email(
            form, subject, body, delegating_user.email, 'external_delegation_reply'
        )

    @staticmethod
    def send_final_approval_with_amount_change(form, recipient_user, remarks=""):
        """Send special final approval email when amount is changed (to submitter)"""
        try:
            template = EmailNotificationTemplate.objects.get(
                event_type='final_approved_amount_changed',
                is_active=True
            )
            
            context = {
                'form': form,
                'submitter_name': recipient_user.get_full_name() or recipient_user.username,
                'remarks': remarks or "No remarks provided by final approver.",
                'form_detail_link': f"{settings.SITE_URL}/form/{form.id}/" if hasattr(settings, 'SITE_URL') else "",
            }

            subject = template.subject
            body = template.body

            # Render template
            subject_template = Template(subject)
            body_template = Template(body)

            rendered_subject = subject_template.render(Context(context))
            rendered_body = body_template.render(Context(context))

            send_mail(
                subject=rendered_subject,
                message="Please use an HTML compatible email client.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_user.email],
                fail_silently=False,
                html_message=rendered_body,
            )
            logger.info(f"✅ Amount change email sent to submitter {recipient_user.email} for form {form.form_number}")
        except EmailNotificationTemplate.DoesNotExist:
            logger.error("Email template 'final_approved_amount_changed' not found or inactive")
        except Exception as e:
            logger.error(f"Failed to send amount change email to submitter: {e}", exc_info=True)

    @staticmethod
    def send_amount_change_notification_to_approvers(form, recipient_user, remarks=""):
        """Notify previous approvers about final amount change"""
        try:
            template = EmailNotificationTemplate.objects.get(
                event_type='amount_changed_notify_approvers',
                is_active=True
            )
            
            context = {
                'form': form,
                'approver_name': recipient_user.get_full_name() or recipient_user.username,
                'remarks': remarks or "No remarks provided.",
            }

            subject = template.subject
            body = template.body

            from django.template import Template, Context
            subject_template = Template(subject)
            body_template = Template(body)

            rendered_subject = subject_template.render(Context(context))
            rendered_body = body_template.render(Context(context))

            send_mail(
                subject=rendered_subject,
                message="Please use an HTML compatible email client.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_user.email],
                fail_silently=False,
                html_message=rendered_body,
            )
            logger.info(f"✅ Amount change notification sent to approver {recipient_user.email}")
        except EmailNotificationTemplate.DoesNotExist:
            logger.error("Email template 'amount_changed_notify_approvers' not found or inactive")
        except Exception as e:
            logger.error(f"Failed to send amount change notification to approver: {e}", exc_info=True)