"""
approval_workflow/workflows.py
Main Approval Workflow Engine for SMVS Approval System
This file contains the core business logic for:
- Determining approval rules
- Progressing through approval levels
- Handling submit, approve, reject, delegation and revision flows
- Sending notifications using EmailNotificationService + new NotificationService for SMS
"""

from django.utils import timezone
from django.conf import settings
import threading
from approval_core.models import (
    ApprovalForm,
    ApprovalAction,
    ApprovalLevel,
    ApprovalRule,
    RuleApprovalSequence,
    ApprovalLevelUser,
    SMSTemplate,
    EmailNotificationTemplate,
)
from approval_core.services import EmailNotificationService
from approval_core.notification_service import send_notification_for_approval_level, NotificationService
from django.db.models import Q
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ApprovalWorkflowEngine:
    """Main Approval Workflow Engine with Email + TextGuru SMS support"""

    @staticmethod
    def determine_applicable_rule(form):
        logger.info(f"[RULE] Checking rule for form {form.form_number} | Amount: ₹{form.amount}")
        active_rules = ApprovalRule.objects.filter(is_active=True).order_by("-priority")

        # ✅ Always compare in INR for rule matching
        # For foreign currency forms, use amount_inr (the converted INR value)
        # For INR forms, use amount directly
        comparison_amount = (
            form.amount_inr
            if (getattr(form, 'currency_code', 'INR') != 'INR' and form.amount_inr)
            else form.amount
        )
        logger.info(
            f"[RULE] Comparison amount (INR): ₹{comparison_amount} "
            f"| Original: {getattr(form, 'currency_symbol', '₹')}{form.amount}"
        )

        # ── Priority 0: Country-based rule (foreign currency forms only) ──────
        # This runs BEFORE all existing rules and does NOT affect INR forms.
        if form.submitted_by:
            try:
                user_role = form.submitted_by.approval_role
                country = None
                if user_role.department and user_role.department.country:
                    country = user_role.department.country
                elif (
                    user_role.center
                    and hasattr(user_role.center, 'zone')
                    and user_role.center.zone
                    and user_role.center.zone.country
                ):
                    country = user_role.center.zone.country

                if country and getattr(country, 'currency_code', 'INR') != 'INR':
                    country_rule = ApprovalRule.objects.filter(
                        applicable_country=country,
                        is_active=True
                    ).first()
                    if country_rule:
                        logger.info(
                            f"[RULE COUNTRY] Country rule '{country_rule.rule_name}' "
                            f"matched for country '{country.name}' "
                            f"(chain_type={country_rule.chain_type})"
                        )
                        return country_rule
            except Exception as e:
                logger.warning(f"[RULE COUNTRY] Could not detect country rule: {e}")

        # 🟢 SMART SOURCE-BASED FILTERING ENGINE
        # Explicitly evaluate how this form was originally birthed in the database.
        # This keeps Center workflows completely isolated from Department workflows.
        is_center_initiated = (form.center_id is not None or form.selected_center_id is not None)

        # ── Existing rules — use comparison_amount (INR) for matching ─────────
        for rule in active_rules:
            # Skip country-specific rules in standard matching
            if rule.applicable_country:
                continue

            # 💡 EXPLICIT GATING RULE 1:
            # If the form started from a Center, skip rules meant for Departments
            if is_center_initiated and "dept" in rule.rule_name.lower():
                logger.info(f"[RULE SKIP] Skipping department rule '{rule.rule_name}' for center-initiated form.")
                continue

            # 💡 EXPLICIT GATING RULE 2:
            # If the form started from a Department, skip rules meant for Centers
            if not is_center_initiated and "center" in rule.rule_name.lower():
                logger.info(f"[RULE SKIP] Skipping center rule '{rule.rule_name}' for department-initiated form.")
                continue

            if rule.rule_type in ["amount", "combined"]:
                if rule.min_amount and comparison_amount < rule.min_amount:
                    continue
                if rule.max_amount and comparison_amount > rule.max_amount:
                    continue
            if rule.rule_type in ["department", "combined"]:
                if rule.applicable_departments.exists():
                    if not form.department:
                        continue
                    if form.department not in rule.applicable_departments.all():
                        continue

            logger.info(f"[RULE SUCCESS] '{rule.rule_name}' matched (INR comparison: ₹{comparison_amount})")
            return rule

        # ── Fallback for center forms with no department ──────────────────────
        if not form.department and is_center_initiated:
            logger.info(f"[RULE] Center form with no dept — trying center amount-only rules as fallback")
            
            for rule in active_rules:
                if rule.applicable_country:
                    continue
                
                # 💡 SAFE GATING: Skip any rule meant for departments even in fallback mode
                if "dept" in rule.rule_name.lower():
                    continue

                if rule.rule_type == "amount" or "center" in rule.rule_name.lower():
                    if rule.min_amount and comparison_amount < rule.min_amount:
                        continue
                    if rule.max_amount and comparison_amount > rule.max_amount:
                        continue
                    logger.info(f"[RULE FALLBACK] Center rule '{rule.rule_name}' matched safely for center form")
                    return rule

            # Last Resort: Fallback ONLY to a dedicated center rule instead of blindly grabbing the first row
            center_fallback = active_rules.filter(rule_name__icontains="center", applicable_country__isnull=True).first()
            if center_fallback:
                logger.info(f"[RULE LAST RESORT] Using center fallback rule '{center_fallback.rule_name}'")
                return center_fallback

        logger.warning(f"[RULE] No rule matched for form {form.form_number}")
        return None

    @staticmethod
    def get_next_approval_level(form, current_level=None):
        if not form.applicable_rule:
            return None
        sequences = RuleApprovalSequence.objects.filter(rule=form.applicable_rule).order_by("sequence_order")
        if current_level is None:
            first = sequences.first()
            return first.approval_level if first else None
        current_seq = sequences.filter(approval_level=current_level).first()
        if not current_seq:
            return None
        next_seq = sequences.filter(sequence_order__gt=current_seq.sequence_order).first()
        return next_seq.approval_level if next_seq else None


    @staticmethod
    def dispatch_notifications_async(form_id, current_level_id, assigned_dept_id=None):
        """
        ========================================================================
        ⚡ CORE WORKFLOW SEQUENTIAL WORKER (TRACK 1) - TEMPLATE-DRIVEN FIXED
        ========================================================================
        - Dynamically resolves actionable level email templates.
        - Fetches and compiles SMS text bodies from the database (SMSTemplate).
        - Safely falls back to system strings if database rows are missing.
        """

        try:
            form = ApprovalForm.objects.get(id=form_id)
            current_level = ApprovalLevel.objects.get(id=current_level_id) if current_level_id else None
            
            if not current_level:
                return

            logger.info(f"[ASYNC WORKER] Processing Form {form.form_number} at tier {current_level.level_name} | Explicit Dept ID: {assigned_dept_id}")

            # Query active users for this level
            approvers = ApprovalLevelUser.objects.filter(approval_level=current_level, is_active=True)
            
            # 🟢 FIX 1: Changed departments_id to departments__id for Many-to-Many relation accuracy
            if assigned_dept_id:
                approvers = approvers.filter(Q(departments__id=int(assigned_dept_id)) | Q(departments__isnull=True))

            email_addresses = []
            notifier = NotificationService()
            template = EmailNotificationService.get_template('pending_approval')

            # 🟢 DYNAMIC LOOKUP: Fetch active SMS Template from DB for this event layer
            db_sms_template = SMSTemplate.objects.filter(
                Q(event_type__icontains='pending_approval') | Q(event_type__icontains='WKF-01'),
                is_active=True
            ).first()

            today = timezone.now().strftime('%d-%m-%Y')
            current_time = timezone.now().strftime('%H:%M')

            for alu in approvers:
                user = alu.user
                if not user or user == form.submitted_by:
                    continue 

                if user.email:
                    email_addresses.append(user.email.strip())

                phone = getattr(user.user_profile, 'phone', None) if hasattr(user, 'user_profile') else None
                if phone:
                    # 🟢 INJECT DYNAMIC CONTEXT TRANSFORMS FOR BACKUP SMS
                    if db_sms_template:
                        try:
                            sms_text = db_sms_template.message_text.format(
                                form_number=form.form_number,
                                subject=form.subject,
                                amount=form.amount,
                                approver_name=user.get_full_name() or user.username,
                                remarks="Pending your action review."
                            )
                        except KeyError:
                            # Safe inline string formatting fallback if named bracket placeholders mismatch
                            sms_text = f"FM={form.form_number} SUB={form.subject} Pending your approval level D={today} T={current_time}-RJPSWM"
                    else:
                        # Standard hardcoded fallback if no rows exist in the database table yet
                        sms_text = f"FM={form.form_number} SUB={form.subject} Pending your approval level D={today} T={current_time}-RJPSWM"

                    try:
                        notifier.send_sms(phone, sms_text)
                    except Exception as e: 
                        logger.error(f"[TRACK 1 SMS ERROR]: {e}")

                    try:
                        notifier.send_dynamic_whatsapp_by_event(
                            event_type='pending_approval',
                            approval_form=form,
                            recipient_phone=phone,
                            approver_name=user.get_full_name() or user.username
                        )
                    except Exception as e: 
                        logger.error(f"[TRACK 1 WA ERROR]: {e}")

            unique_emails = list(set(email_addresses))
            if unique_emails and template:
                email_string = ",".join(unique_emails)
                
                context_data = {
                    'form': form,
                    'user': approvers.first().user if approvers.exists() else form.submitted_by,
                    'login_url': f"{settings.SITE_URL}/form/{form.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{form.id}/",
                    'approval_link_text': getattr(template, 'approval_link_text', 'Click here to review and approve')
                }
                
                compiled_subject = EmailNotificationService.render_template(template.subject, context_data)
                compiled_body = EmailNotificationService.render_template(template.body, context_data)
                
                notifier.send_email(email_string, compiled_subject, compiled_body)
                logger.info(f"[TRACK 1 SUCCESS] Actionable intermediate email sent to: {email_string}")

        except Exception as async_global_err:
            logger.error(f"[ASYNC CRITICAL FAILURE] Sequential thread failed: {async_global_err}", exc_info=True)


    @staticmethod
    def submit_form(form, submitted_by):
        """
        ========================================================================
        ⚡ METHOD 2: submit_form ENGINE (UI THREAD)
        ========================================================================
        SECTION 1: WORKFLOW INITIALIZATION & BASELINE STATE CAPTURE
        """
        logger.info(f"[SUBMIT START] Form {form.form_number} by {submitted_by.username}")

        is_resubmission = form.status == "revision_pending"

        form.status = "submitted"
        form.submitted_by = submitted_by
        form.submitted_at = timezone.now()
        form.applicable_rule = ApprovalWorkflowEngine.determine_applicable_rule(form)

        """
        ========================================================================
        SECTION 2: ROUTING LAYER (FRESH SUBMISSION VS. DYNAMIC REVISION GATING)
        ========================================================================
        """
        if is_resubmission:
            form.status = "pending"
            
            if form.applicable_rule and form.department is not None:
                sabhya_seq = RuleApprovalSequence.objects.filter(
                    rule=form.applicable_rule,
                    approval_level__level_name__icontains="sabhay"
                ).first()
                
                if sabhya_seq:
                    form.current_approval_level = sabhya_seq.approval_level
                    logger.info(f"[SUBMIT REVISION] Department exists ({form.department.name}). Routing straight to MK Sabhya.")
                else:
                    form.current_approval_level = RuleApprovalSequence.objects.filter(rule=form.applicable_rule).order_by("sequence_order").first().approval_level
            else:
                first_seq = RuleApprovalSequence.objects.filter(
                    rule=form.applicable_rule
                ).order_by("sequence_order").first()
                
                if first_seq:
                    form.current_approval_level = first_seq.approval_level
                    logger.info(f"[SUBMIT REVISION] No department selected yet. Routing back to Operator for initial verification.")
                    
        elif form.applicable_rule:
            first_seq = RuleApprovalSequence.objects.filter(
                rule=form.applicable_rule
            ).order_by("sequence_order").first()
            
            if first_seq:
                form.current_approval_level = first_seq.approval_level
                form.status = "pending"
                logger.info(f"[SUBMIT FRESH] Normal track assigned to level: {first_seq.approval_level.level_name}")

        form.save()

        """
        ========================================================================
        SECTION 3: TIMELINE AUDITING LOG CREATION
        ========================================================================
        """
        ApprovalAction.objects.create(
            form=form,
            action_type="resubmitted" if is_resubmission else "submitted",
            actor=submitted_by,
            remarks=f"{'Revision Resubmitted' if is_resubmission else 'Submitted'} by {submitted_by.get_full_name() or submitted_by.username}"
        )

        """
        ========================================================================
        SECTION 4: ACTIVE TARGET LEVEL COMMUNICATION ROUTINE (⚡ FIRE-AND-FORGET)
        ========================================================================
        if form.current_approval_level:
            logger.info(f"[SUBMIT ENGINE] Offloading alerts to background worker threads asynchronously...")
            
            threading.Thread(
                target=ApprovalWorkflowEngine.dispatch_notifications_async,
                args=(
                    form.id, 
                    form.current_approval_level.id, 
                    form.department.id if form.department else None
                )
            ).start()
        """

        logger.info(f"[SUBMIT SUCCESS] Form {form.form_number} successfully entered active state.")
        return form


    @staticmethod
    def approve_form(form, user, remarks="", delegate_to=None, approved_amount=None):
        """
        ========================================================================
        TITLE: MASTER WORKFLOW PROGRESSION ENGINE (APPROVE ACTION)
        ========================================================================
        - Handles safety checks, audit trail logging, and dynamic rule processing.
        - Section 1: Initial Safety Audits & History Logging
        - Section 2: Dynamic Revision Return Gating vs. Next Tier Discovery Loop
        - Section 3: Final Workflow Termination (WKF-03 / WKF-04 Matrix CC Blast)
        - Section 4: Automated Evaluation Gateway (Auto-Approval / Async Worker Trigger)
        """
        
        # ========================================================================
        # SECTION 1: INITIAL SAFETY AUDITS & HISTORY LOGGING
        # ========================================================================
        logger.info(f"===== [APPROVE START] Form {form.form_number} by {user.username} =====")

        # Safety Check: If current level was cleared incorrectly or lost mid-transit
        if not form.current_approval_level:
            logger.error(f"[APPROVE ERROR] current_approval_level is None for form {form.form_number}. Cannot approve.")
            raise ValueError("Cannot approve: current approval level is not set.")

        current_level_name = form.current_approval_level.level_name.lower()

        # Create the permanent history row for the timeline
        ApprovalAction.objects.create(
            form=form,
            actor=user,
            action_type="approved",
            remarks=remarks,
            approval_level=form.current_approval_level,
        )

        # Verify that an active budget routing rule is attached to the form context
        if not form.applicable_rule:
            logger.error(f"[APPROVE ERROR] No rule assigned to form {form.form_number}")
            raise ValueError(f"No approval rule assigned to form {form.form_number}. Cannot proceed.")


        # ========================================================================
        # SECTION 2: DYNAMIC REVISION RETURN GATING VS. DISCOVERY LOOP
        # ========================================================================
        target_next_level = None
        is_returning_from_revision = False

        # Intercept if the clearing tier is MK Sabhya or MK Sabhay
        if "sabhay" in current_level_name or "sabhya" in current_level_name:
            
            # Find the absolute latest action taken by anyone on this form
            latest_global_action = form.actions.order_by("-created_at").first()
            
            # SMART AUDIT GUARD: Look for the most recent revision request
            last_revision_request = form.actions.filter(
                action_type="revision_requested"
            ).order_by("-created_at").first()

            if last_revision_request and last_revision_request.approval_level:
                
                # Check if any standard progression occurred after the revision request
                has_intervening_progression = form.actions.filter(
                    created_at__gt=last_revision_request.created_at,
                    action_type__in=["approved", "delegated", "approved_by_internal", "approved_by_external"]
                ).exclude(id=latest_global_action.id if latest_global_action else None).exists()

                # Only route backward if the revision request hasn't been cleared by intermediate steps
                if not has_intervening_progression:
                    target_next_level = last_revision_request.approval_level
                    is_returning_from_revision = True
                    logger.info(f"[REVISION RETURN TRACK] Valid active revision return found. Routing back to: {target_next_level.level_name}")

            if not is_returning_from_revision:
                logger.info(f"[REVISION IGNORED] Historical revision request was already cleared by standard progression tracks. Moving forward.")

        # Standard sequential path: executed if this is a standard forward workflow step
        if not is_returning_from_revision:
            next_level = ApprovalWorkflowEngine.get_next_approval_level(form, form.current_approval_level)

            # Loop through subsequent sequences to find a tier with real assigned approvers
            while next_level is not None:
                approvers = ApprovalLevelUser.objects.filter(
                    approval_level=next_level,
                    is_active=True
                )
                if form.department:
                    approvers = approvers.filter(
                        Q(departments=form.department) | Q(departments__isnull=True)
                    )

                if approvers.exists():
                    break  # Valid operational tier found — stop scanning
                else:
                    logger.info(f"[APPROVE] Level {next_level.level_name} has NO active users for dept {form.department} → skipping")
                    skipped = next_level
                    next_level = ApprovalWorkflowEngine.get_next_approval_level(form, skipped)
            
            target_next_level = next_level

        is_final = target_next_level is None

        # 🟢 PRE-RESOLVE THREAD-SAFE PRIMITIVES ON MAIN REQUEST EXECUTION LOOP
        # This completely stops database transaction isolation and race conditions!
        form_pk = form.id
        submitter_email = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None
        next_level_id = target_next_level.id if target_next_level else None
        final_department_id = form.department_id


        # ========================================================================
        # SECTION 3: FINAL WORKFLOW TERMINATION (COMPLETED CHAIN)
        # ========================================================================
        if is_final:
            form.status = "approved"
            form.approved_at = timezone.now()
            form.current_approval_level = None

            # Safe Decimal casting conversion for amount variations
            from decimal import Decimal as D
            amount_changed = False
            
            if approved_amount is not None:
                approved_decimal = D(str(approved_amount))
                form.approved_amount = approved_decimal
                amount_changed = approved_decimal != form.amount
                logger.info(f"[FINAL APPROVAL] Approved amount set to ₹{approved_decimal} (requested: ₹{form.amount}), changed={amount_changed}")
            else:
                form.approved_amount = form.amount
                amount_changed = False

            # Convert and save local currency elements for foreign forms
            if form.currency_code != 'INR' and form.exchange_rate_used and form.exchange_rate_used > 0:
                from approval_core.currency_service import convert_from_inr
                form.approved_amount_local = convert_from_inr(form.approved_amount, form.exchange_rate_used)

            form.save()
            logger.info(f"[FINAL APPROVAL] Form {form.form_number} is now FINAL APPROVED")

            # 📨 ASYNC SUB-PIPELINE 2: UNIFIED FINAL BROADCAST (WKF-03 / WKF-04)
            def execute_final_approval_matrix_async(f_id, s_email, has_amount_shifted, final_remarks):
                from approval_core.models import ApprovalForm, EmailNotificationTemplate
                from approval_core.services import EmailNotificationService
                from approval_core.utils import process_matrix_notification

                f_obj = ApprovalForm.objects.get(id=f_id)
                if s_email:
                    # Determine which database row identity code to look up dynamically
                    target_event_code = 'WKF-04' if has_amount_shifted else 'WKF-03'
                    
                    template_matrix = EmailNotificationTemplate.objects.filter(
                        Q(event_type__icontains=target_event_code) | Q(event_type__icontains='final_approved'),
                        is_active=True
                    ).first()

                    if template_matrix:
                        context_matrix = {
                            'form': f_obj,
                            'submitted_by': f_obj.submitted_by,
                            'user': f_obj.submitted_by,
                            'remarks': final_remarks or "No remarks provided by final approver.",
                            'login_url': f"{settings.SITE_URL}/form/{f_obj.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{f_obj.id}/",
                        }
                        compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                        compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                    else:
                        compiled_subject = f"Final Approval Completed - {f_obj.form_number}"
                        compiled_body = f"Your form {f_obj.form_number} has been final approved."

                    # Send unified email packet where Submitter is TO and Matrix Posts are CC
                    process_matrix_notification(f_obj, target_event_code, compiled_subject, compiled_body, cc_email=s_email)
                    logger.info(f"[TRACK 2 SUCCESS] Final approval matrix email sent completely.")

                    # Handle background notifications to previous sequence approvers regarding amount shifts (WKF-05)
                    if has_amount_shifted:
                        try:
                            previous_approver_ids = ApprovalAction.objects.filter(
                                form=f_obj, action_type="approved"
                            ).exclude(actor=f_obj.submitted_by).values_list('actor_id', flat=True).distinct()

                            for approver_id in previous_approver_ids:
                                approver = User.objects.get(id=approver_id)
                                if approver.email and approver != f_obj.submitted_by:
                                    EmailNotificationService.send_amount_change_notification_to_approvers(
                                        form=f_obj, recipient_user=approver, remarks=final_remarks or "No remarks provided"
                                    )
                        except Exception as inner_e: 
                            logger.error(f"[WKF-05 ERROR] Failed telling previous approvers: {inner_e}")

            # Spawn final matrix broadcast safely in the background
            threading.Thread(
                target=execute_final_approval_matrix_async,
                args=(form_pk, submitter_email, amount_changed, remarks)
            ).start()

            # 📱 Dispatch final approval text notifications (SMS via TextGuru)
            try:
                send_notification_for_approval_level(
                    approval_form=form, approval_level=None, action='final_approved', approver_user=user
                )
            except Exception as sms_e:
                logger.error(f"[SMS ERROR] Final approval SMS failed: {sms_e}", exc_info=True)

            logger.info(f"===== [FINAL APPROVAL END] Form {form.form_number} successfully processed =====")
            return True


        # ========================================================================
        # SECTION 4: TRANSITION TO TARGET TIER & AUTOMATED EVALUATION GATEWAY
        # ========================================================================
        # Advance sequence state parameters
        form.current_approval_level = target_next_level
        form.status = "pending"
        form.save()
        logger.info(f"[APPROVE] Form assigned to target level: {target_next_level.level_name}")

        # RECURSIVE ENGINE TRIGGER: Evaluate Auto-Approval rules on the targeted tier instantly
        sequence_rule = RuleApprovalSequence.objects.filter(rule=form.applicable_rule, approval_level=target_next_level).first()
        if sequence_rule and sequence_rule.auto_approve_if_conditions_met:
            logger.info(f"[AUTO-APPROVE] Level {target_next_level.level_name} has auto-approve enabled. Re-routing instantly...")
            
            # Recursively re-run approve_form automatically using a system-signature flag
            return ApprovalWorkflowEngine.approve_form(
                form=form,
                user=user,  # Passes forward the active user footprint as authorization
                remarks=remarks,
                approved_amount=approved_amount
            )

        # ── Standard Non-Automated Routing: Trigger alerts in background worker ──
        if next_level_id:
            threading.Thread(
                target=ApprovalWorkflowEngine.dispatch_notifications_async,
                args=(form_pk, next_level_id, final_department_id)
            ).start()

        logger.info(f"===== [APPROVE END] Form {form.form_number} successfully forwarded =====")
        return True

    @staticmethod
    def reject_form(form, approver, remarks="", allow_revision=True):
        """
        ========================================================================
        🏆 UNIFIED REJECTION & REVISION ENGINE (WKF-02 / WKF-06)
        ========================================================================
        - Dynamically processes Revision Requests (WKF-02) or Permanent Rejections (WKF-06).
        - Automatically resolves and dispatches across Email, SMS, and WhatsApp.
        - Avoids duplicate code methods by operating cleanly on the allow_revision flag.
        """
        logger.info(f"[REJECT START] Form {form.form_number} by {approver.username}")

        # Create history audit log entry using accurate choice keywords matching backend schemas
        ApprovalAction.objects.create(
            form=form,
            action_type="revision_requested" if allow_revision else "rejected",
            approval_level=form.current_approval_level,
            actor=approver,
            remarks=remarks
        )

        if allow_revision:
            form.status = "revision_pending"
            target_event_code = 'WKF-02'
            # 🟢 SYMMETRICAL ACTION KEY: Matches 'revision_requested' in your notification_service mapping condition!
            sms_action_key = 'revision_requested'
        else:
            form.status = "rejected"
            form.rejected_at = timezone.now()
            form.current_approval_level = None   
            target_event_code = 'WKF-06'
            # 🟢 SYMMETRICAL ACTION KEY: Matches 'rejected' in your notification_service mapping condition!
            sms_action_key = 'rejected'
            
        form.save()

        # 🟢 PRE-RESOLVE THREAD-SAFE PRIMITIVES ON MAIN REQUEST EXECUTION LOOP
        form_pk = form.id
        submitter_email = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None
        current_level_obj = form.current_approval_level

        # ==============================================================================
        # 📨 ASYNC PIPELINE 2: UNIFIED REJECTION / REVISION MATRIX COUPLING
        # ==============================================================================
        def execute_rejection_matrix_async(form_id, event_code, op_remarks):
            from approval_core.models import ApprovalForm, EmailNotificationTemplate
            from approval_core.services import EmailNotificationService
            from approval_core.utils import process_matrix_notification

            f_obj = ApprovalForm.objects.get(id=form_id)
            s_email = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None
            
            if s_email:
                template_matrix = EmailNotificationTemplate.objects.filter(
                    Q(event_type__icontains=event_code),
                    is_active=True
                ).first()

                if template_matrix:
                    context_matrix = {
                        'form': f_obj,
                        'submitted_by': f_obj.submitted_by, # 💡 Fixed from 'user'
                        'approver': approver,                # 💡 Added missing approver key
                        'remarks': op_remarks or "No remarks provided.",
                        'login_url': f"{settings.SITE_URL}/form/{f_obj.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{f_obj.id}/",
                    }

                    compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                    compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                else:
                    fallback_label = "Revision Requested" if event_code == 'WKF-02' else "Permanently Rejected"
                    compiled_subject = f"{fallback_label} - {f_obj.form_number}"
                    compiled_body = f"Your form {f_obj.form_number} status changed to: {fallback_label}."

                # Dispatch unified matrix TO / CC layout
                process_matrix_notification(f_obj, event_code, compiled_subject, compiled_body, cc_email=s_email)
                logger.info(f"[TRACK 2 SUCCESS] Rejection matrix alert blasted for: {event_code}")

        # Spawn matrix email notification track safely in background
        threading.Thread(
            target=execute_rejection_matrix_async,
            args=(form_pk, target_event_code, remarks)
        ).start()
        # ==============================================================================

        # 📱 Dispatch text alerts (SMS via TextGuru & WhatsApp via Pinbot.ai)
        try:
            logger.info(f"[CELLULAR] Dispatching {sms_action_key} notification stream via cell services")
            send_notification_for_approval_level(
                approval_form=form,
                approval_level=current_level_obj,
                action=sms_action_key,
                approver_user=approver
            )
        except Exception as sms_e:
            logger.error(f"[CELLULAR ERROR] Failed to send text/whatsapp notifications: {sms_e}", exc_info=True)

        logger.info(f"[REJECT SUCCESS] Form status processed successfully as {form.status}")
        return form


    # ====================== DELEGATION ======================
    @staticmethod
    def delegate_form(form, delegating_user, delegated_to_user, reason=""):
        """
        ========================================================================
        📌 INTERNAL DELEGATION MODULE (DLG-01)
        ========================================================================
        Delegates to an internal 3rd Party Verifier and dispatches via Matrix CC.
        """
        logger.info(f"[DELEGATE] Form {form.form_number} delegated by {delegating_user.username} to {delegated_to_user.username}")
        
        real_delegator_level = form.current_approval_level
        notified_time = timezone.now() 

        sequence = RuleApprovalSequence.objects.filter(
            rule=form.applicable_rule,
            approval_level=form.current_approval_level
        ).first()
        if not sequence or not getattr(sequence, 'allow_delegation', False):
            logger.error(f"[DELEGATE ERROR] Delegation not allowed at this level for form {form.form_number}")
            raise PermissionError("Delegation not allowed")

        form.delegated_by = delegating_user
        form.delegated_to = delegated_to_user
        form.is_delegated = True
        form.status = "delegated"
        form.save()

        ApprovalAction.objects.create(
            form=form,
            action_type="delegated",
            approval_level=real_delegator_level,
            actor=delegating_user,
            delegated_to=delegated_to_user,
            delegation_reason=reason,
            remarks=reason or "Delegated to 3rd Party Verifier"
        )

        # 🟢 PRE-RESOLVE THREAD-SAFE PRIMITIVES FOR MATRIX CC EMAIL
        form_pk = form.id
        submitter_email = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None

        def execute_delegation_matrix_async(f_id, s_email, op_reason):
            from approval_core.models import ApprovalForm, EmailNotificationTemplate
            from approval_core.services import EmailNotificationService
            from approval_core.utils import process_matrix_notification

            f_obj = ApprovalForm.objects.get(id=f_id)
            if s_email:
                template_matrix = EmailNotificationTemplate.objects.filter(
                    Q(event_type__icontains='DLG-01'), is_active=True
                ).first()

                context_matrix = {
                    'form': f_obj,
                    'user': f_obj.submitted_by,
                    'remarks': op_reason or "Delegated to 3rd Party Verifier.",
                    'login_url': f"{settings.SITE_URL}/form/{f_obj.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{f_obj.id}/",
                }
                
                if template_matrix:
                    compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                    compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                else:
                    compiled_subject = f"Form Internal Delegation - {f_obj.form_number}"
                    compiled_body = f"Your form {f_obj.form_number} has been delegated to an internal verifier."

                process_matrix_notification(f_obj, 'DLG-01', compiled_subject, compiled_body, cc_email=s_email)

        threading.Thread(
            target=execute_delegation_matrix_async,
            args=(form_pk, submitter_email, reason)
        ).start()

        # 📱 Dispatch Text & WhatsApp Notifications
        try:
            logger.info(f"[CELLULAR] Sending delegated notification to {delegated_to_user.username}")
            send_notification_for_approval_level(
                approval_form=form,
                approval_level=real_delegator_level,
                action='delegated',  # 👈 Key matches 'delegated' in EVENT_CHOICES
                approver_user=delegating_user
            )
        except Exception as sms_e:
            logger.error(f"[CELLULAR ERROR] Failed to send delegated alerts: {sms_e}", exc_info=True)

        logger.info(f"[DELEGATE SUCCESS] Form {form.form_number} delegated successfully.")
        return True


    @staticmethod
    def delegate_form_external(form, delegating_user, external_email, reason=""):
        """
        ========================================================================
        📌 EXTERNAL DELEGATION MODULE (DLG-02)
        ========================================================================
        Delegates to an external guest email and dispatches across all channels.
        """
        if not form.current_approval_level:
            raise ValueError("No current approval level defined for this form")
            
        real_delegator_level = form.current_approval_level
        notified_time = timezone.now()
        
        sequence = RuleApprovalSequence.objects.filter(
            rule=form.applicable_rule,
            approval_level=form.current_approval_level
        ).first()
        if not sequence or not getattr(sequence, 'allow_delegation', False):
            raise PermissionError("Delegation is not allowed at this approval level")
            
        import secrets
        form.guest_token = secrets.token_urlsafe(48)
        form.delegated_by = delegating_user
        form.delegated_to = None
        form.delegated_email = external_email
        form.is_delegated = True
        form.status = "delegated"
        form.save()
        
        ApprovalAction.objects.create(
            form=form,
            action_type="delegated",
            approval_level=real_delegator_level,
            actor=delegating_user,
            delegation_reason=reason,
            notified_at=notified_time,
            remarks=reason or f"Delegated to external: {external_email}"
        )
        
        # 🟢 PRE-RESOLVE THREAD-SAFE PRIMITIVES FOR MATRIX CC EMAIL
        form_pk = form.id
        submitter_email = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None

        def execute_external_matrix_async(f_id, s_email, op_reason):
            from approval_core.models import ApprovalForm, EmailNotificationTemplate
            from approval_core.services import EmailNotificationService
            from approval_core.utils import process_matrix_notification

            f_obj = ApprovalForm.objects.get(id=f_id)
            if s_email:
                template_matrix = EmailNotificationTemplate.objects.filter(
                    Q(event_type__icontains='DLG-02'), is_active=True
                ).first()

                context_matrix = {
                    'form': f_obj,
                    'user': f_obj.submitted_by,
                    'remarks': op_reason or f"Delegated to external email: {external_email}",
                    'login_url': f"{settings.SITE_URL}/form/{f_obj.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{f_obj.id}/",
                }
                
                if template_matrix:
                    compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                    compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                else:
                    compiled_subject = f"Form External Delegation - {f_obj.form_number}"
                    compiled_body = f"Your form {f_obj.form_number} has been delegated to guest verifier: {external_email}"

                process_matrix_notification(f_obj, 'DLG-02', compiled_subject, compiled_body, cc_email=s_email)

        threading.Thread(
            target=execute_external_matrix_async,
            args=(form_pk, submitter_email, reason)
        ).start()

        # 📱 🟢 FIXED: Added missing cellular tracker mapping to 'external_delegated' key!
        try:
            send_notification_for_approval_level(
                approval_form=form,
                approval_level=real_delegator_level,
                action='external_delegated',  # 👈 Key matches 'external_delegated' in EVENT_CHOICES
                approver_user=delegating_user
            )
        except Exception as cell_err:
            logger.error(f"[CELLULAR ERROR] External delegation text dispatch failed: {cell_err}")

        logger.info(f"Form {form.form_number} delegated externally to {external_email}")
        return True


    @staticmethod
    def return_from_delegation(form, verifier_user=None, remarks="", decision="approved"):
        """
        ========================================================================
        📌 DELEGATION RETURN MODULE (DLG-03 / DLG-04 / DLG-05 / DLG-06)
        ========================================================================
        Returns form to the original delegator with accurate internal/external keys.
        """
        if not form.is_delegated or not form.delegated_by:
            raise ValueError("This form was not delegated")

        logger.info(f"[DELEGATION RETURN] Form {form.form_number} returned with decision: {decision}")

        notified_time = timezone.now()
        original_delegator = form.delegated_by
        original_level = form.current_approval_level
        is_external = bool(form.delegated_email)

        # 🟢 FIXED: Map dynamic choice keywords to match EVENT_CHOICES explicitly!
        if is_external:
            if decision == "approved":
                action_type = "approved_by_external"   # 👈 Key matches 'approved_by_external'
                matrix_event_code = 'DLG-04'
            elif decision == "rejected":
                action_type = "rejected_by_external"   # 👈 Key matches 'rejected_by_external'
                matrix_event_code = 'DLG-06'
            else:
                action_type = "delegation_returned"
                matrix_event_code = 'DLG-07'
        else:
            if decision == "approved":
                action_type = "approved_by_internal"   # 👈 Key matches 'approved_by_internal'
                matrix_event_code = 'DLG-03'
            elif decision == "rejected":
                action_type = "rejected_by_internal"   # 👈 Key matches 'rejected_by_internal'
                matrix_event_code = 'DLG-05'
            else:
                action_type = "delegation_returned"
                matrix_event_code = 'DLG-07'

        delegation_reason = (
            f"DELEGATED_BY:{original_delegator.get_full_name() or original_delegator.username}"
            f"|DELEGATED_BY_EMAIL:{original_delegator.email or ''}"
            f"|EXTERNAL_EMAIL:{form.delegated_email or ''}"
            f"|LEVEL:{original_level.level_name if original_level else 'Unknown'}"
        )

        ApprovalAction.objects.create(
            form=form,
            action_type=action_type,
            approval_level=original_level,
            actor=verifier_user,
            remarks=remarks,
            notified_at=notified_time,
            delegation_reason=delegation_reason
        )
 
        # Reset delegation properties
        original_level_id = original_level.id if original_level else None
        form.delegated_to = None
        form.delegated_email = None
        form.is_delegated = False
        form.status = "pending"
        
        if original_level_id:
            from approval_core.models import ApprovalLevel
            form.current_approval_level = ApprovalLevel.objects.get(id=original_level_id)
        else:
            form.current_approval_level = original_level
        form.save()

        # 🟢 PRE-RESOLVE THREAD-SAFE PRIMITIVES FOR MATRIX CC EMAIL
        form_pk = form.id
        submitter_email = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None

        def execute_return_matrix_async(f_id, s_email, e_code, op_remarks):
            from approval_core.models import ApprovalForm, EmailNotificationTemplate
            from approval_core.services import EmailNotificationService
            from approval_core.utils import process_matrix_notification

            f_obj = ApprovalForm.objects.get(id=f_id)
            if s_email:
                template_matrix = EmailNotificationTemplate.objects.filter(
                    Q(event_type__icontains=e_code), is_active=True
                ).first()

                context_matrix = {
                    'form': f_obj,
                    'user': f_obj.submitted_by,
                    'remarks': op_remarks or f"Delegation returned with verdict: {decision}.",
                    'login_url': f"{settings.SITE_URL}/form/{f_obj.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{f_obj.id}/",
                }
                
                if template_matrix:
                    compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                    compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
                else:
                    compiled_subject = f"Delegation Return Update ({e_code}) - {f_obj.form_number}"
                    compiled_body = f"Delegation response received for form {f_obj.form_number}. Verdict: {decision}"

                process_matrix_notification(f_obj, e_code, compiled_subject, compiled_body, cc_email=s_email)

        threading.Thread(
            target=execute_return_matrix_async,
            args=(form_pk, submitter_email, matrix_event_code, remarks)
        ).start()

        # 📱 Dispatch Symmetrical SMS & WhatsApp Templates
        try:
            logger.info(f"[CELLULAR] Sending return notification to {original_delegator.username}")
            send_notification_for_approval_level(
                approval_form=form,
                approval_level=original_level,
                action=action_type,  # 👈 🟢 FIXED: action now perfectly passes your backend model keys!
                approver_user=verifier_user or original_delegator,
            )
        except Exception as sms_e:
            logger.error(f"[CELLULAR ERROR] Delegation return text dispatch failed: {sms_e}", exc_info=True)

        logger.info(f"[DELEGATION RETURN SUCCESS] Form returned safely to delegator {original_delegator.username}")
        return True


    @staticmethod
    def request_resubmission(form, approver, remarks=""):
        """
        Upgraded Revision Requested Engine with Matrix CC Support (WKF-02)
        """
        logger.info(f"[RESUBMIT REQUEST] Form {form.form_number} sent back by {approver.username}")

        # Protect against duplicate logs if caught via standard reject_form redirect
        already_logged = form.actions.filter(action_type="revision_requested", actor=approver, remarks=remarks).exists()
        if not already_logged:
            ApprovalAction.objects.create(
                form=form,
                action_type="revision_requested",
                approval_level=form.current_approval_level,
                actor=approver,
                remarks=remarks
            )

        form.status = "revision_pending"
        form.is_delegated = False
        form.delegated_to = None
        form.delegated_by = None
        form.save()

        # 🟢 UNIFIED MATRIX BROADCAST FOR REVISION REQUESTS (WKF-02)
        submitter_mail = form.submitted_by.email.strip() if form.submitted_by and form.submitted_by.email else None
        if submitter_mail:
            template_matrix = EmailNotificationTemplate.objects.filter(
                Q(event_type__icontains='WKF-02') | Q(event_type__icontains='revision'),
                is_active=True
            ).first()

            if template_matrix:
                context_matrix = {
                    'form': form,
                    'submitted_by': form.submitted_by,
                    'approver': approver,
                    'remarks': remarks,
                    'login_url': f"{settings.SITE_URL}/form/{form.id}/" if hasattr(settings, 'SITE_URL') else f"/form/{form.id}/",
                }
                compiled_subject = EmailNotificationService.render_template(template_matrix.subject, context_matrix)
                compiled_body = EmailNotificationService.render_template(template_matrix.body, context_matrix)
            else:
                compiled_subject = f"Revision/Resubmission Requested - {form.form_number}"
                compiled_body = f"Revision has been requested for form {form.form_number}."

            from approval_core.utils import process_matrix_notification
            threading.Thread(
                target=process_matrix_notification,
                args=(form, 'WKF-02', compiled_subject, compiled_body, submitter_mail)
            ).start()

        try:
            send_notification_for_approval_level(
                approval_form=form, approval_level=None, action='revision_requested', approver_user=approver
            )
        except Exception as sms_e: logger.error(f"[SMS ERROR]: {sms_e}")

        return True