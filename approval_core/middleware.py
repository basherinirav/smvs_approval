import json
import logging
from django.utils.deprecation import MiddlewareMixin
from django.utils import timezone
from django.shortcuts import redirect
from django.urls import reverse
from approval_core.models import AuditLog

logger = logging.getLogger(__name__)


class ScreenLockMiddleware:
    """✅ NEW: Gatekeeper that blocks access if the screen is locked"""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Check if the session is locked
        is_locked = request.session.get('is_locked', False)
        
        # 2. Define URLs that are ALWAYS allowed (to avoid infinite loops)
        # Ensure these names match your urls.py exactly
        allowed_urls = [
            reverse('lock_screen'),
            reverse('logout'),
            reverse('login'),
        ]

        # 3. If locked and trying to access a restricted page, force redirect back to lock screen
        if is_locked and request.path not in allowed_urls and not request.path.startswith('/static/'):
            return redirect(f"{reverse('lock_screen')}?next={request.path}")

        return self.get_response(request)


class AuditLoggingMiddleware(MiddlewareMixin):
    """Middleware to log all user actions (login/logout/CRUD operations)"""

    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def process_request(self, request):
        """Log login attempts"""
        if request.path == '/admin/login/' and request.method == 'POST':
            username = request.POST.get('username', 'Unknown')
            try:
                from django.contrib.auth.models import User
                user = User.objects.get(username=username)
                # Log will be created in process_response if login successful
                request._audit_login_attempt = True
                request._audit_username = username
            except Exception as e:
                logger.debug(f"Login audit: {e}")

        return None

    def process_response(self, request, response):
        """Log successful login and other actions"""
        try:
            # Log successful login
            if hasattr(request, '_audit_login_attempt') and response.status_code == 302:
                # 302 redirect indicates successful login
                from django.contrib.auth.models import User
                try:
                    user = User.objects.get(username=request._audit_username)
                    AuditLog.objects.create(
                        action='login',
                        user=user,
                        ip_address=self.get_client_ip(request),
                        description=f"User {user.username} logged in"
                    )
                except User.DoesNotExist:
                    pass

            # Log logout
            if request.path == '/admin/logout/' and request.user.is_authenticated:
                AuditLog.objects.create(
                    action='logout',
                    user=request.user,
                    ip_address=self.get_client_ip(request),
                    description=f"User {request.user.username} logged out"
                )

        except Exception as e:
            logger.error(f"Audit logging error: {e}")

        return response
