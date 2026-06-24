from django.core.mail import EmailMessage
from django.conf import settings
import logging
from .models import NotificationRoutingMatrix, EmailMapping
from approval_core.notification_service import NotificationService

logger = logging.getLogger(__name__)

def process_matrix_notification(form, event_type, compiled_subject, compiled_body, cc_email=None):
    """
    Master Matrix Router.
    - Resolves all active target roles cleanly without variable mismatches.
    - If cc_email is provided, it places the submitter in the TO line and matrix posts in CC.
    """
    try:
        # Wildcard-safe lookup to find the matrix configuration row cleanly
        matrix = NotificationRoutingMatrix.objects.filter(event_type__icontains=event_type).first()
        if not matrix:
            logger.warning(f"[MATRIX ERROR] Matrix row for {event_type} not found.")
            return
    except Exception as e:
        logger.error(f"[MATRIX CRITICAL ERROR] Query failed: {e}")
        return

    email_recipients = []
    whatsapp_recipients = []
    sms_recipients = []

    # 1. Base Target Post Resolution Arrays
    active_email_posts = []
    active_wa_posts = []
    active_sms_posts = []
    entity_filter = {}

    # 🟢 Cross check center relationship structure cleanly using real working mapping keys
    if form.center or form.selected_center:
        target_center = form.center or form.selected_center
        entity_filter = {'mapping_type': 'center', 'center': target_center}
        
        if matrix.email_center_accountant: active_email_posts.append("Center Accountant")
        if matrix.email_center:            active_email_posts.append("Center")
        if matrix.email_center_sant:       active_email_posts.append("Center Sant")
        if matrix.email_prabhari_sant:     active_email_posts.append("Prabhari Sant")
        if matrix.email_zonal_head:        active_email_posts.append("Zonal Head") # 🟢 FIXED VARIABLE NAME MATCH
        
    elif form.department:
        entity_filter = {'mapping_type': 'department', 'department': form.department}
        
        if matrix.email_department:         active_email_posts.append("Department")
        if matrix.email_dept_leader_sant:   active_email_posts.append("Dept Leader Sant")
        if matrix.email_dept_sant:          active_email_posts.append("Dept Sant")
        if matrix.email_hod:                active_email_posts.append("HOD")
        if matrix.email_mk_haribhakt:       active_email_posts.append("MK Haribhakt")
        if matrix.email_mk_sant:            active_email_posts.append("MK Sant") # 🟢 FIXED VARIABLE NAME MATCH
        if matrix.email_secretary:          active_email_posts.append("Secretary")

    # Resolve cellular tracking configurations
    if matrix.wa_center_accountant:    active_wa_posts.append("Center Accountant")
    if matrix.wa_center:               active_wa_posts.append("Center")
    if matrix.wa_center_sant:          active_wa_posts.append("Center Sant")
    if matrix.wa_prabhari_sant:        active_wa_posts.append("Prabhari Sant")

    if matrix.sms_center_accountant:   active_sms_posts.append("Center Accountant")
    if matrix.sms_center:              active_sms_posts.append("Center")
    if matrix.sms_center_sant:         active_sms_posts.append("Center Sant")
    if matrix.sms_prabhari_sant:       active_sms_posts.append("Prabhari Sant")

    total_posts_requested = list(set(active_email_posts + active_wa_posts + active_sms_posts))
    
    if entity_filter and total_posts_requested:
        try:
            mappings = EmailMapping.objects.filter(
                is_active=True, 
                post__role_name__in=total_posts_requested, 
                **entity_filter
            ).select_related('post')
            
            for m in mappings:
                role_name = m.post.role_name
                if role_name in active_email_posts and m.email:
                    email_recipients.extend([e.strip() for e in m.email.split(',') if e.strip()])
                if m.phone_number:
                    if role_name in active_wa_posts: whatsapp_recipients.append(m.phone_number)
                    if role_name in active_sms_posts: sms_recipients.append(m.phone_number)
        except Exception as query_err:
            logger.error(f"[MATRIX QUERY ERROR] Mapping lookup failed: {query_err}")

    email_recipients = list(set(email_recipients))
    whatsapp_recipients = list(set(whatsapp_recipients))
    sms_recipients = list(set(sms_recipients))

    notifier = NotificationService()

    # 2. 📨 EXECUTE TO/CC DISPATCH ENGINE COUPLING
    if cc_email:
        try:
            # Send exactly ONE email packet containing TO and CC combined
            msg = EmailMessage(
                subject=compiled_subject,
                body=compiled_body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'manjuripatra@in.smvs.org'),
                to=[cc_email.strip()],
                cc=email_recipients
            )
            msg.content_subtype = "html"
            msg.send(fail_silently=False)
            logger.info(f"[MATRIX SUCCESS] Sent unified email TO: {cc_email} | CC: {email_recipients}")
        except Exception as mail_err:
            logger.error(f"[MATRIX MAIL FAILURE] CC delivery failed: {mail_err}")
    
    elif email_recipients:
        # Fallback tracking if called without a target CC email parameter
        try:
            msg = EmailMessage(
                subject=compiled_subject,
                body=compiled_body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'manjuripatra@in.smvs.org'),
                to=email_recipients
            )
            msg.content_subtype = "html"
            msg.send(fail_silently=False)
        except Exception as mail_err:
            logger.error(f"[MATRIX FALLBACK FAILURE]: {mail_err}")

    # 3. Cellular Streams
    if whatsapp_recipients:
        for phone in whatsapp_recipients:
            try: notifier.send_dynamic_whatsapp_by_event(event_type, form, phone)
            except Exception as e: logger.error(f"[MATRIX WA ERR]: {e}")

    if sms_recipients:
        for phone in sms_recipients:
            try: notifier.send_sms(phone, compiled_body)
            except Exception as e: logger.error(f"[MATRIX SMS ERR]: {e}")