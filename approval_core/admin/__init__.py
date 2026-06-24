"""
approval_core/admin/__init__.py
Central admin registration - Prevents duplicate registration errors
"""

from django.contrib import admin

# Import all admin modules
from .masters import *
from .pre_approval import *
from .approval_process import *
from .logs import *
from .auth_admin import *

# ====================== SAFETY NET FOR ApprovalLevelUser ======================

try:
    from ..models import ApprovalLevelUser
    # Unregister if already registered
    admin.site.unregister(ApprovalLevelUser)
except admin.sites.NotRegistered:
    pass
except Exception:
    pass

# Re-register with our custom admin (with auto logic)
from .pre_approval import ApprovalLevelUserAdmin
admin.site.register(ApprovalLevelUser, ApprovalLevelUserAdmin)

print("[OK] approval_core admin loaded successfully (duplicate registration prevented)")