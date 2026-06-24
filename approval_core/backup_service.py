import os
import logging
from django.utils import timezone
from django.contrib.auth.models import User
from approval_core.models import (
    ApprovalForm, ApprovalAction, ApprovalDocument, 
    ApprovalComment, BackupRestoreLog, Department, Center
)

logger = logging.getLogger(__name__)

class ProductionBackupEngine:
    
    @staticmethod
    def record_log(action_type, filename, user, status, error_msg="", file_path=None):
        """
        ========================================================================
        📋 PRODUCTION SNAPSHOT LOGGER & METRIC PARSER
        ========================================================================
        Computes granular structural item statistics across tables to provide
        complete transparency regarding what exact kind of backup was taken.
        """
        file_size = 0.0
        file_exists = False
        
        if file_path and os.path.exists(file_path):
            file_size = round(os.path.getsize(file_path) / 1024.0, 2)  # Conversion to KB
            file_exists = True
            
        # Compile granular database metrics to record what kind of data was saved
        current_users = User.objects.count()
        current_forms = ApprovalForm.objects.count()
        current_actions = ApprovalAction.objects.count()
        current_docs = ApprovalDocument.objects.count()
        current_comments = ApprovalComment.objects.count()
        current_depts = Department.objects.count()
        current_centers = Center.objects.count()
        
        # Build an explicit descriptive summary manifest report
        summary_text = f"==================================================\n"
        summary_text += f"   SMVS APPROVAL SYSTEM - DETAILED BACKUP MANIFEST\n"
        summary_text += f"==================================================\n"
        summary_text += f"📅 Timestamp      : {timezone.now().strftime('%d-%m-%Y %H:%M:%S')} IST\n"
        summary_text += f"📁 Target File    : {filename}\n"
        summary_text += f"📍 Physical Path  : {file_path if file_path else 'Not Specified'}\n"
        summary_text += f"⚡ Disk Status     : {'FILE WRITTEN TO STORAGE' if (file_exists and file_size > 0) else '⚠️ CRITICAL: FILE NOT FOUND OR EMPTY ON DISK'}\n"
        summary_text += f"📦 Registered Size: {file_size} KB\n\n"
        
        summary_text += f"--- STRUCTURE-WIDE DATABASE CONTENT ANALYSIS ---\n"
        summary_text += f" 👤 Total User Accounts (Credentials & Roles) : {current_users}\n"
        summary_text += f" 📄 Total Approval Forms (Core Requests)     : {current_forms}\n"
        summary_text += f" 🔄 Total Workflow History Action Timeline Rows: {current_actions}\n"
        summary_text += f" 📎 Total Uploaded Document Metadata Links   : {current_docs}\n"
        summary_text += f" 💬 Total Form Remarks & Visibility Comments : {current_comments}\n"
        summary_text += f" 🏢 Total Registered Organizational Depts     : {current_depts}\n"
        summary_text += f" 🗺️ Total Active Center Branch Nodes          : {current_centers}\n"
        summary_text += f"--------------------------------------------------\n"
        
        if error_msg:
            summary_text += f"\n❌ EXECUTION EXCEPTION DETAILED FAILURE:\n{error_msg}\n"
            
        # Save into the automated logging model database table space
        log_entry = BackupRestoreLog.objects.create(
            action_type=action_type,
            filename=filename,
            file_size_kb=file_size,
            executed_by=user,
            status='success' if (status == 'success' and file_size > 0) else 'failed',
            user_count_verified=current_users,
            form_count_verified=current_forms,
            log_summary=summary_text
        )
        return log_entry

    @staticmethod
    def verify_restored_data(log_entry):
        """Executes a post-restore audit comparing data row parity counts."""
        post_users = User.objects.count()
        post_forms = ApprovalForm.objects.count()
        
        audit_text = f"\n\n==================================================\n"
        audit_text += f"🔄 POST-RESTORE SYSTEM ACCOUNTABILITY AUDIT\n"
        audit_text += f"==================================================\n"
        audit_text += f"Expected User Accounts: {log_entry.user_count_verified} | Restored: {post_users}\n"
        audit_text += f"Expected Application Forms: {log_entry.form_count_verified} | Restored: {post_forms}\n"
        
        if post_users >= log_entry.user_count_verified and post_forms >= log_entry.form_count_verified:
            log_entry.status = 'success'
            audit_text += "✨ VERIFICATION STATUS: SUCCESS - Parity checks passed cleanly.\n"
        else:
            log_entry.status = 'failed'
            audit_text += "⚠️ VERIFICATION STATUS: CRITICAL FAILURE - Missing system table rows!\n"
            
        log_entry.log_summary += audit_text
        log_entry.save()