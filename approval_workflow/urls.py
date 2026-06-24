from django.urls import path
from django.views.generic import RedirectView
from . import views
from .views import health_check, master_zip_import_view, verify_admin_password, check_admin_session_status
from approval_workflow.views import (
    approval_report_view,
    save_actual_expenditure_view,
)

urlpatterns = [
    # Home redirect
    path("", RedirectView.as_view(url="dashboard/", permanent=False), name="home"),

    # Universal Master One-Click Tool Path Track
    path('system/master-zip-import/', views.master_zip_import_view, name='master_zip_import'),
    path('system/master-file-stream-import/', views.master_file_stream_import_view, name='master_file_stream_import'),

    # The Gatekeeper Interceptor "Ask Option"
    path('forms/initialize/', views.initialize_form, name='initialize_form'),

    # The Actual Form Generation Entry View
    path('forms/create-workspace/', views.create_approval_form_workspace, name='create_approval_form_workspace'),

    # Authentication
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path('healthz/', health_check, name='health_check'),

    # Forgot Password Workflow
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),
    path('verify-otp/', views.verify_otp_view, name='verify_otp'),
    path('change-password/', views.change_password_view, name='change_password'),
    path('lock/', views.lock_screen_view, name='lock_screen'),
    path('ajax/check-registration-email/', views.check_registration_email_ajax, name='check_registration_email_ajax'),

    # 🛡️ Upgraded Live Dynamic Session & Validation Gates
    path('verify-admin-password/', views.verify_admin_password, name='verify_admin_password'),
    path('api/check-admin-session/', views.check_admin_session_status, name='check_admin_session'),

    # Dashboard & Forms
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("form/<int:form_id>/", views.form_detail_view, name="form_detail"),
    path("form/<int:form_id>/submit/", views.submit_form_view, name="submit_form"),
    path("form/<int:form_id>/upload/", views.upload_document_view, name="upload_document"),
    path("forms/", views.forms_list_view, name="forms_list"),
    path('form/<int:form_id>/edit/', views.edit_form_view, name='edit_form'), 

    # Approval Actions
    path("form/<int:form_id>/approve/", views.approve_form_view, name="approve_form"),
    path("form/<int:form_id>/reject/", views.reject_form_view, name="reject_form"),

    # Guest (EXTERNAL)
    path('guest/form/<str:token>/', views.guest_form_view, name='guest_form'),
    path('guest/form/<str:token>/approve/', views.guest_approve_form_view, name='guest_approve_form'),

    # Comments & Backup
    path('form/<int:form_id>/add_comment/', views.add_comment_view, name='add_comment'),
    path('backup/full/', views.backup_full_view, name='backup_full'),
    path('backup/db/', views.backup_db_view, name='backup_db'),
    path('restore-backup/', views.restore_backup_view, name='restore_backup'),

    # Report
    path('report/', approval_report_view, name='approval_report'),
    path('report/actual/<int:form_id>/save/', save_actual_expenditure_view,  name='save_actual_expenditure'),
    path('reports/oversight/', views.prabhari_report_view, name='prabhari_report_view'),

    path('help/', views.help_manual_view, name='help_manual'),
    path('core-reference/', views.core_system_reference_view, name='core_reference'),
    path("verify-document/<int:doc_id>/", views.verify_document, name="verify_document"),    
]