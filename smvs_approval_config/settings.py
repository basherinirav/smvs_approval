"""
Django settings for smvs_approval_config project.
"""

from pathlib import Path
import os
from decouple import config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET_KEY", default="dev-secret-key")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config("DEBUG", default=False, cast=bool)

# 🟢 FIXED: Parse host strings dynamically out of the environment configuration layer securely
raw_hosts = config("ALLOWED_HOSTS", default="localhost,127.0.0.1")
ALLOWED_HOSTS = [host.strip() for host in raw_hosts.split(",") if host.strip()]

# Application definition
INSTALLED_APPS = [
    "approval_workflow",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_crontab",
    "import_export",
    "approval_core.apps.ApprovalCoreConfig",        
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "approval_core.middleware.AuditLoggingMiddleware",
    "approval_core.middleware.ScreenLockMiddleware", # 🔒 Enforce the lock screen
]

ROOT_URLCONF = "smvs_approval_config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "approval_core.context_processors.nav_permissions",
                "approval_core.context_processors.active_workspace_processor",
            ],
        },
    },
]

WSGI_APPLICATION = "smvs_approval_config.wsgi.application"

# Database Connection Router Layout
DATABASES = {
    "default": {
        "ENGINE": config('DB_ENGINE', default='django.db.backends.sqlite3'),
        "NAME": config('DB_NAME', default=str(BASE_DIR / 'db.sqlite3')),
        "USER": config('DB_USER', default=''),
        "PASSWORD": config('DB_PASSWORD', default=''),
        "HOST": config('DB_HOST', default=''),
        "PORT": config('DB_PORT', default=''),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

ADMIN_PANEL_PASSWORD = config('ADMIN_PANEL_PASSWORD', default='')

# Internationalization
LANGUAGE_CODE = "en-IN"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_L10N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

# Static files configuration
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Media files configuration
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ====================== EMAIL CONFIGURATION ======================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=465, cast=int)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=True, cast=bool)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=False, cast=bool)

# Secured credential references via environment
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="")

SITE_URL = config("SITE_URL", default="http://localhost:9000")

# Authentication Redirect Routes
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom settings for Approval Workflow
APPROVAL_LEVELS = {
    1: "Operator",
    2: "MK Sabhya",
    3: "MK Sant 1",
    4: "MK Sant 2",
    5: "HDH Guruji",
    6: "3rd Party Verification",
}

APPROVAL_STATUS_CHOICES = {
    "initiated": "Application Initiated",
    "submitted": "Submitted by End User",
    "pending_operator": "Pending Operator Review",
    "rejected_operator": "Rejected by Operator",
    "revision_pending": "Revision Pending from End User",
    "pending_mk_sabhya": "Pending MK Sabhya Approval",
    "rejected_mk_sabhya": "Rejected by MK Sabhya",
    "pending_mk_sant": "Pending MK Sant 1 Approval",
    "rejected_mk_sant": "Rejected by MK Sant 1",
    "pending_p_rajipaswami": "Pending MK Sant 2 Approval",
    "rejected_p_rajipaswami": "Rejected by MK Sant 2",
    "pending_hdh_guruji": "Pending HDH Guruji Approval",
    "approved": "Approved by HDH Guruji",
    "rejected": "Rejected",
}

# File upload restrictions
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB

# Session Lifecycle Parameters
SESSION_COOKIE_AGE = 7200  # ✅ Session expires after 2 hours of total time (7200 seconds)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True # ✅ Session expires when the user closes their browser
SESSION_SAVE_EVERY_REQUEST = True # ✅ Updates the cookie on every request so the 2-hour window resets with activity

# Regional Formatting Options
DATE_FORMAT = "d-m-Y"
DATETIME_FORMAT = "d-m-Y H:i"
SHORT_DATE_FORMAT = "d-m-Y"
SHORT_DATETIME_FORMAT = "d-m-Y H:i"

# 🟢 FIXED: Dynamic parsing loops for trusted origins list arrays safely
raw_origins = config("CSRF_TRUSTED_ORIGINS", default="http://localhost:9000,http://127.0.0.1:9000")
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
TRUSTED_ENTERPRISE_DOMAINS_REGEX = r'@([a-z0-9-]+\.)*(smvs\.org|smvs\.in|smvs\.org\.in)$'

CSRF_COOKIE_SECURE = False     
SESSION_COOKIE_SECURE = False

# ==============================================================================
# 💬 WHATSAPP / SMS GATEWAY CONFIGURATION
# ==============================================================================
WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY", "")
WHATSAPP_FROM_NUMBER = os.environ.get("WHATSAPP_FROM_NUMBER", "")
WHATSAPP_BASE_URL = os.environ.get("WHATSAPP_BASE_URL", "")

TEXTGURU_LOGINID = config('TEXTGURU_LOGINID', default='')
TEXTGURU_PASSWORD = config('TEXTGURU_PASSWORD', default='')
TEXTGURU_SENDERID = config('TEXTGURU_SENDERID', default='')
TEXTGURU_API_URL = 'https://www.txtguru.in/imobile/api.php'

ENABLE_SMS_NOTIFICATIONS = config('ENABLE_SMS_NOTIFICATIONS', default=True, cast=bool)
ENABLE_WHATSAPP_NOTIFICATIONS = config('ENABLE_WHATSAPP_NOTIFICATIONS', default=True, cast=bool)

APPROVAL_LEVEL_NOTIFICATION_CONFIG = {
    "operator": {
        "channels": ["email", "sms"],
        "template": "operator_approval",
    },
    "mk_sabhya": {
        "channels": ["email", "sms", "whatsapp"],
        "template": "mk_sabhya_approval",
    },
    "mk_sant": {
        "channels": ["email", "sms", "whatsapp"],
        "template": "mk_sant_approval",
    },
    "p_rajipaswami": {
        "channels": ["email", "sms", "whatsapp"],
        "template": "p_rajipaswami_approval",
    },
    "hdh_guruji": {
        "channels": ["email", "sms", "whatsapp"],
        "template": "hdh_guruji_approval",
    },
}

# ====================== LOGGING CONFIGURATION ======================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'approval_workflow': {          # workflows.py
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'approval_core': {              # models, services, sms_service
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'approval_core.sms_service': {  # Specifically for TextGuru SMS
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}

# ====================== CRON JOBS ======================
CRONJOBS = [
    # Daily Database Backup at 2:00 AM
    ('0 2 * * *', 'django.core.management.call_command', ['backup_db']),

    # Weekly Full Project Backup every Sunday at 3:00 AM
    ('0 3 * * 0', 'django.core.management.call_command', ['backup_project']),
]

CRONTAB_COMMAND_SUFFIX = '>> /backups/smvs_cron_execution.log 2>&1'

# ====================== CENTRAL PRODUCTION BACKUP STORAGE ======================
BACKUP_DIRECTORY = config('BACKUP_DIRECTORY', default='/backups')

try:
    if not os.path.exists(BACKUP_DIRECTORY):
        os.makedirs(BACKUP_DIRECTORY, exist_ok=True)
    print(f"✅ Backup directory set to: {BACKUP_DIRECTORY}")
except Exception as e:
    print(f"⚠️ Warning: Could not initialize backup directory folder: {e}")