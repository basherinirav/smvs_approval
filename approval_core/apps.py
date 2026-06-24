from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class ApprovalCoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'approval_core'
    
    def ready(self):
        # Import admin registrations
        import approval_core.admin
        
        # Import signals so they get connected at startup
        import approval_core.signals
        
        logger.info("✅ ApprovalCore signals and admin loaded successfully")