import re
import locale
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import MinValueValidator, FileExtensionValidator
import urllib.request
import json
import os
from django.core.exceptions import ValidationError


# ==================== Master Data Models ====================

class Country(models.Model):
    """Country Master"""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    # ✅ Added phone_code to store calling codes permanently in the DB
    phone_code = models.CharField(
        max_length=10, 
        blank=True, 
        null=True, 
        help_text="International telephone prefix code (e.g., 91, 94, 61)"
    )

    # ✅ Currency info per country
    currency_code = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text="ISO 4217 currency code e.g. USD, GBP, AED, INR"
    )
    currency_symbol = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text="Currency symbol e.g. $, £, د.إ, ₹"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        # Updated to reflect your new format requirement for the registration template list dropdown
        return f"{self.name} (+{self.phone_code})" if self.phone_code else self.name

    class Meta:
        verbose_name_plural = "Countries"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        """
        Dynamically fetches and auto-populates country configurations (phone codes, currency info)
        from a global REST API on save, using the alpha country code for 100% accurate mapping.
        """
        import urllib.request
        import urllib.parse
        import json

        # Clean and standardize the shortcode string formatting
        if self.code:
            self.code = self.code.strip().upper()

        # Only execute network lookups if data is missing or defaults need replacing
        if self.code and (not self.phone_code or not self.currency_code or self.currency_code == 'INR'):
            try:
                # 🟢 CHANGED: Query by alpha code (e.g., /alpha/aus) instead of name string
                url = f"https://restcountries.com/v3.1/alpha/{self.code.lower()}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    
                    if data and isinstance(data, list):
                        country_data = data[0]
                        
                        # 1. Extract Dynamic Calling Phone Code
                        idd_info = country_data.get('idd', {})
                        root_code = idd_info.get('root', '').replace('+', '')  # e.g., "1" or "9"
                        suffixes = idd_info.get('suffixes', [])
                        
                        if root_code:
                            # Handle unified prefixes vs split region structures safely
                            suffix_code = suffixes[0] if len(suffixes) == 1 else ""
                            calculated_code = f"{root_code}{suffix_code}"
                            if not self.phone_code:
                                self.phone_code = calculated_code

                        # 2. Extract Dynamic Currency Parameters
                        currencies_dict = country_data.get('currencies', {})
                        if currencies_dict:
                            # Fetch the primary currency object code key (e.g., AUD, USD)
                            curr_code = list(currencies_dict.keys())[0]
                            curr_details = currencies_dict[curr_code]
                            
                            # Update if empty or resetting a fallback value
                            if not self.currency_code or self.currency_code == 'INR':
                                self.currency_code = curr_code
                            if not self.currency_symbol or self.currency_symbol == '₹':
                                self.currency_symbol = curr_details.get('symbol', '₹')
                                
            except Exception as e:
                # Fallback graceful handler to make sure database saves aren't blocked if offline
                print(f"Dynamic country data auto-population skipped due to code lookup error: {e}")

        # Execute standard model database save sequence
        super(Country, self).save(*args, **kwargs)


class ExchangeRate(models.Model):
    """Daily exchange rates against INR — fetched live, cached per day"""
    currency_code = models.CharField(max_length=10, unique=True)
    currency_symbol = models.CharField(max_length=10, default='')
    rate_to_inr = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        help_text="1 unit of this currency = X INR e.g. 1 USD = 83.50 INR"
    )
    fetched_date = models.DateField(default=timezone.now)
    fetched_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"1 {self.currency_code} = ₹{self.rate_to_inr} (as of {self.fetched_date})"

    class Meta:
        verbose_name = "Exchange Rate"
        verbose_name_plural = "Exchange Rates"
        ordering = ['currency_code']


class Zone(models.Model):
    """Zone Master - Country → Zone"""
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="zones")
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"

    class Meta:
        verbose_name_plural = "Zones"
        unique_together = [["country", "code"]]
        ordering = ["name"]


class Center(models.Model):
    """Center Master - Zone → Center"""
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="centers")
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=10, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"

    class Meta:
        verbose_name_plural = "Centers"
        unique_together = [["zone", "code"]]
        ordering = ["name"]


class Department(models.Model):
    """Department Master - Country → Department (Independent)"""
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="departments", null=True, blank=True)
    center = models.ForeignKey(Center, on_delete=models.SET_NULL, null=True, blank=True, related_name="department_assignments")
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    requires_center_selection = models.BooleanField(
        default=False,
        help_text="If enabled, end users from this department must select a center while creating a form."
    )

    def __str__(self):
        return f"{self.code} - {self.name}"

    class Meta:
        verbose_name_plural = "Departments"
        unique_together = [["country", "code"]]
        ordering = ["name"]


class PostMaster(models.Model):
    """Post - Email role configuration for Center and Department"""
    POST_TYPE_CHOICES = [
        ("center", "Center Post"),
        ("department", "Department Post"),
    ]
    
    post_type = models.CharField(max_length=20, choices=POST_TYPE_CHOICES)
    role_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.role_name} ({self.get_post_type_display()})"

    class Meta:
        verbose_name_plural = "Posts"
        unique_together = [["post_type", "role_name"]]
        ordering = ["post_type", "role_name"]


class EmailMapping(models.Model):
    """Email Mapping - Map email addresses to Post roles for Center/Department"""
    MAPPING_TYPE_CHOICES = [
        ("center", "Center"),
        ("department", "Department"),
    ]
    
    post = models.ForeignKey(PostMaster, on_delete=models.CASCADE, related_name="email_mappings")
    mapping_type = models.CharField(max_length=20, choices=MAPPING_TYPE_CHOICES)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, null=True, blank=True, related_name="email_mappings")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, null=True, blank=True, related_name="email_mappings")
    email = models.EmailField()
    person_name = models.CharField(max_length=255, blank=True, null=True)
    phone_number = models.CharField(
        max_length=15, 
        blank=True, 
        null=True, 
        help_text="WhatsApp phone number with country code for this post role (e.g., 919876543210)"
    )
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        entity = self.center.name if self.center else self.department.name
        return f"{entity} - {self.post.role_name} ({self.email})"

    class Meta:
        verbose_name_plural = "Email Mappings"
        ordering = ["email"]


class EmailNotificationTemplate(models.Model):
    """Dynamic email templates editable by admin with support for links and variables"""

    # Event types for auto-triggering
    EVENT_TYPE_CHOICES = [
        # 📁 USER ACCOUNTS & REGISTRATION SERIES (REG)
        ("reg_leader_pending", "REG-01: External User Pending Local Center Leader Review"),
        ("reg_admin_pending", "REG-02: User Verified by Center Leader Awaiting Approval of Admin"),
        ("reg_user_activated", "REG-03: User Account Activated successfully"),
        ("reg_user_declined", "REG-04: User Account Registration Declined"),
        ("new_user_registered", "REG-05: Internal New User Registered Awaiting Approval of Admin"),

        # 📁 APPLICATION BASE LIFECYCLE SERIES (FRM)
        ("form_submitted", "FRM-01: Form Successfully Submitted"),

        # 📁 ACTIVE WORKFLOW MOVEMENTS SERIES (WKF)
        ("pending_approval", "WKF-01: Pending Approval (Forwarded to Next Level)"),
        ("revision_requested", "WKF-02: Revision/Resubmission Requested"),
        ("final_approved", "WKF-03: Final Approval Completed"),
        ("final_approved_amount_changed", "WKF-04: Final Approval with Amount Change (To Submitter)"),
        ("amount_changed_notify_approvers", "WKF-05: Amount Changed Notification (To Previous Approvers)"),
        ("rejected", "WKF-06: Form Permanently Rejected"),

        # 📁 WORKFLOW DELEGATION EXTENSIONS SERIES (DLG)
        ("delegated", "DLG-01: Internal Delegation to 3rd Party"),
        ("external_delegated", "DLG-02: External Delegation to Guest Email"),
        ("approved_by_internal", "DLG-03: 3rd Party Verified (Internal)"),
        ("approved_by_external", "DLG-04: 3rd Party Verified (External)"),
        ("rejected_by_internal", "DLG-05: 3rd Party Rejected (Internal)"),
        ("rejected_by_external", "DLG-06: 3rd Party Rejected (External)"),
        ("delegation_returned", "DLG-07: Delegation Formally Returned"),  
        ("external_delegation_reply", "DLG-08: External Delegation Reply Received"),

        # 🔒 SECURITY & SYSTEM CORE SERIES (SYS)
        ("otp_sent", "SYS-01: Password Reset OTP Transmission"),
    ]

    CONTEXT_MODEL_CHOICES = [
        ("approval_form", "Approval Form"),
    ]

    template_name = models.CharField(max_length=255, unique=True, help_text="Internal name, e.g., '[REG-01] Vasna Center Version'")
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)
    
    subject = models.CharField(max_length=500)
    body = models.TextField(help_text="Use Django template variables: {{ form.form_number }}, {{ form.subject }}, {{ form.amount }}, {{ login_url }}, {{ user.get_full_name }}, {{ remarks }}")

    approval_link_text = models.CharField(
        max_length=100, 
        default="Click here to view and approve the form",
        help_text="Text for the approval button/link"
    )

    context_model = models.CharField(max_length=50, choices=CONTEXT_MODEL_CHOICES, default="approval_form")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.template_name} - {self.get_event_type_display()}"

    class Meta:
        verbose_name = "Email Notification Template"
        verbose_name_plural = "Email Notification Templates"
        ordering = ["event_type"]
        
        # 💡 SAFETY FOOTPRINT: Prevents users from adding identical templates for the same event types
        unique_together = (('template_name', 'event_type'),)

class AuditLog(models.Model):
    """Audit log for all system activities"""
    ACTION_CHOICES = [
        ("login", "User Login"),
        ("logout", "User Logout"),
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
        ("form_submission", "Form Submission"),
        ("approval", "Approval"),
        ("rejection", "Rejection"),
        ("email_sent", "Email Sent"),
    ]

    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100, blank=True, null=True)
    model_id = models.IntegerField(blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    old_values = models.JSONField(blank=True, null=True)
    new_values = models.JSONField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    ip_address = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} - {self.model_name} by {self.user}"

    class Meta:
        verbose_name_plural = "Audit Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["model_name", "model_id"]),
        ]


# ==================== Approval Workflow Models ====================

class ApprovalLevel(models.Model):
    """Define Approval Hierarchy Levels"""
    level_number = models.IntegerField(unique=True)
    level_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    show_time_taken = models.BooleanField(
        default=True,
        help_text="Allow approvers to see 'Time Taken' for this level and all previous levels"
    )

    def __str__(self):
        return f"{self.level_number}. {self.level_name}"

    class Meta:
        ordering = ["level_number"]


class ApprovalRule(models.Model):
    """Dynamic Approval Routing Rules"""
    RULE_TYPE_CHOICES = [
        ("amount", "Amount-Based"),
        ("department", "Department-Based"),
        ("combined", "Combined (Amount + Department)"),
    ]

    # ✅ Chain type for country-based rules — does NOT affect existing rules
    CHAIN_TYPE_CHOICES = [
        ('standard',            'Standard (Center / Department based)'),
        ('country_independent', 'A — Country Independent (own chain only)'),
        ('country_first',       'B — Country First, then India Chain'),
        ('country_notify',      'C — India Chain, Country notified at end'),
    ]

    rule_name = models.CharField(max_length=255)
    rule_type = models.CharField(max_length=20, choices=RULE_TYPE_CHOICES)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    applicable_departments = models.ManyToManyField(Department, blank=True)
    approval_levels = models.ManyToManyField(ApprovalLevel, through="RuleApprovalSequence")
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    # ✅ NEW — Country-based routing (optional — leave blank for existing rules)
    applicable_country = models.ForeignKey(
        'Country',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approval_rules',
        help_text=(
            "Optional. Set ONLY for country-specific rules. "
            "Leave blank to keep existing center/department rules unchanged."
        )
    )
    chain_type = models.CharField(
        max_length=30,
        choices=CHAIN_TYPE_CHOICES,
        default='standard',
        help_text=(
            "Only used when 'Applicable Country' is set. "
            "A: Country has its own independent chain. "
            "B: Country operator first, then normal India chain. "
            "C: Normal India chain first, country coordinator notified at end."
        )
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.applicable_country:
            return f"{self.rule_name} [Country: {self.applicable_country.name} | {self.get_chain_type_display()}]"
        return f"{self.rule_name} ({self.rule_type})"

    class Meta:
        ordering = ["-priority", "-created_at"]


class RuleApprovalSequence(models.Model):
    """Define sequence of approvers for each rule with delegation support"""

    rule = models.ForeignKey(
        'ApprovalRule',
        on_delete=models.CASCADE,
        related_name='rule_approval_sequences'
    )
    approval_level = models.ForeignKey(
        'ApprovalLevel',
        on_delete=models.CASCADE
    )
    sequence_order = models.PositiveIntegerField(
        help_text="Order of this approval level in the sequence (1, 2, 3...)"
    )
    is_mandatory = models.BooleanField(
        default=True,
        help_text="Is this level mandatory in the approval process?"
    )
    allow_delegation = models.BooleanField(
        default=False,
        help_text="Allow the approver at this level to delegate to 3rd Party Verifier"
    )
    auto_approve_if_conditions_met = models.BooleanField(
        default=False,
        help_text="Automatically approve if predefined conditions are met"
    )
    
    class Meta:
        ordering = ["sequence_order"]
        unique_together = [["rule", "sequence_order"]]
        verbose_name = "Rule Approval Sequence"
        verbose_name_plural = "Rule Approval Sequences"

    def __str__(self):
        return f"{self.rule.rule_name} → Level {self.sequence_order}: {self.approval_level.level_name}"


class ApprovalForm(models.Model):
    """Main Approval Form/Application"""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('initiated', 'Application Initiated'),
        ('submitted', 'Submitted by End User'),
        ('pending_operator', 'Pending Operator Review'),
        ('rejected_operator', 'Rejected by Operator'),
        ('revision_pending', 'Revision Pending from End User'),
        ('pending_mk_sabhya', 'Pending MK Sabhya Approval'),
        ('rejected_mk_sabhya', 'Rejected by MK Sabhya'),
        ('pending_mk_sant', 'Pending MK Sant 1 Approval'),
        ('rejected_mk_sant', 'Rejected by MK Sant 1'),
        ('pending_p_rajipaswami', 'Pending MK Sant 2 Approval'),
        ('rejected_p_rajipaswami', 'Rejected by MK Sant 2'),
        ('pending_hdh_guruji', 'Pending HDH Guruji Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    form_number = models.CharField(max_length=100, unique=True)
    center = models.ForeignKey(Center, on_delete=models.CASCADE, null=True, blank=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, null=True, blank=True)
    submitted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="submitted_forms")
    subject = models.CharField(max_length=500)
    description = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    # ✅ Multi-currency support
    currency_code = models.CharField(
        max_length=10,
        default='INR',
        help_text="Currency used when form was submitted e.g. USD, GBP, INR"
    )
    currency_symbol = models.CharField(
        max_length=10,
        default='₹',
        help_text="Currency symbol at time of submission e.g. $, £, ₹"
    )
    amount_inr = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="INR equivalent of amount at time of submission"
    )
    exchange_rate_used = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Exchange rate at submission: 1 local currency unit = X INR"
    )

    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # ✅ Approved amount in submitter's local currency (calculated from approved_amount INR)
    approved_amount_local = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Approved amount converted to submitter's local currency using submission rate"
    )
    latest_approval_remark = models.TextField(null=True, blank=True)

    # ✅ ADD THIS:
    selected_center = models.ForeignKey(
        Center,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dept_selected_forms',
        help_text="Center selected by department user"
    )

    status = models.CharField(
        max_length=50, 
        choices=STATUS_CHOICES, 
        default='draft'
    )
    
    delegated_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="delegated_forms"
    )

    delegated_to = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="received_delegations"
    )

    delegated_email = models.EmailField(
        blank=True, 
        null=True,
        help_text="Email address when delegated to external person"
    )

    is_delegated = models.BooleanField(default=False)
    guest_token = models.CharField(max_length=64, blank=True, null=True, unique=True)

    current_approval_level = models.ForeignKey(ApprovalLevel, on_delete=models.SET_NULL, null=True, blank=True)
    current_approver = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="currently_approving_forms")
    applicable_rule = models.ForeignKey(ApprovalRule, on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.form_number} - {self.subject[:50]}"

    class Meta:
        ordering = ["-created_at"]

    # Helper property - Is this the final approval level?
    @property
    def is_final_approval_level(self):
        if not self.applicable_rule or not self.current_approval_level:
            return False
        # Get the last sequence for this rule
        last_sequence = RuleApprovalSequence.objects.filter(
            rule=self.applicable_rule
        ).order_by('-sequence_order').first()
        return last_sequence and last_sequence.approval_level == self.current_approval_level


class ApprovalDocument(models.Model):
    """Document Upload for Approval Form"""
    form = models.ForeignKey(ApprovalForm, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=100)
    file = models.FileField(upload_to="approval_documents/%Y/%m/%d/")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="uploaded_documents")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="verified_documents")

    # ✅ NEW OPERATOR CHECKLIST FIELDS
    PRABHARI_SIGN_CHOICES = [
        ('form', 'Approval Form 1st Page'),
        ('email', 'Email Attachment'),
    ]

    is_prescribed_format = models.BooleanField(default=False, verbose_name="In Prescribed Format?")
    is_signature_ok = models.BooleanField(default=False, verbose_name="Signatures OK?")
    prabhari_sign_location = models.CharField(
        max_length=10, 
        choices=PRABHARI_SIGN_CHOICES, 
        null=True, 
        blank=True,
        verbose_name="Prabhari Sign Location"
    )
    is_mom_attached = models.BooleanField(default=False, verbose_name="MOM Attached?")
    is_other_docs_ok = models.BooleanField(default=False, verbose_name="Plan/Quotation/Other Docs OK?")

    def save(self, *args, **kwargs):
        # Only rename the file if it's a new upload (no ID yet)
        if self.file and not self.id:
            # 1. Clean the Form Number (replace slashes with dashes for file safety)
            form_no = self.form.form_number.replace("/", "-")
                      
            # 2. Calculate Sequence (Count existing docs for THIS form + 1)
            count = ApprovalDocument.objects.filter(form=self.form).count() + 1
            padded_count = f"{count:03d}"

            # 3. Construct Final Name: FormNo - Sequence Approval Document
            ext = os.path.splitext(self.file.name)[1]  # Get .pdf, .jpg, etc.
            new_filename = f"{form_no} - {padded_count} Approval Document{ext}"
            
            # Set the new filename
            self.file.name = new_filename

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.form.form_number} - {self.document_type}"

    class Meta:
        verbose_name_plural = "Approval Documents"
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["is_verified"]),
        ]     


class ApprovalAction(models.Model):
    """Track all actions including delegation"""
    ACTION_TYPES = [
        ("submitted", "Submitted"),
        ("resubmitted", "Resubmitted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("revision_requested", "Revision Requested"),
        ("delegated", "Delegated to 3rd Party Verifier"),
        ("approved_by_external", "3rd Party Verified (External)"), # Add this
        ("approved_by_internal", "3rd Party Verified (Internal)"), # Add this
        ("rejected_by_external", "3rd Party Rejected (External)"), # Add this
        ("rejected_by_internal", "3rd Party Rejected (Internal)"), # Add this
        ("delegation_returned", "Delegation Returned"),  
        ("commented", "Comment Added"),
    ]
    form = models.ForeignKey('ApprovalForm', on_delete=models.CASCADE, related_name="actions")
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    approval_level = models.ForeignKey('ApprovalLevel', on_delete=models.SET_NULL, null=True, blank=True)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="performed_actions")
    remarks = models.TextField(blank=True, null=True)
    delegated_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="delegated_tasks")
    delegation_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True, help_text="When notification/delegation email was sent to this level")

    def __str__(self):
        return f"{self.form.form_number} - {self.action_type} by {self.actor}"

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Approval Actions"


class ApprovalComment(models.Model):
    """Internal comments with hierarchical and selective visibility"""
    form = models.ForeignKey(ApprovalForm, on_delete=models.CASCADE, related_name="comments")
    commented_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="comments_made")
    comment_text = models.TextField()

    is_lesser_approval = models.BooleanField(
        default=False,
        help_text="Mark this if comment is for lesser approved amount"
    )

    # Option 1: Simple checkbox (show to all lower levels)
    show_to_lower_levels = models.BooleanField(
        default=False,
        help_text="If checked, all lower levels (including End User) can see this comment"
    )

    # Option 2: Selective visibility - Core Member / Higher can choose specific levels
    visible_to_levels = models.ManyToManyField(
        'ApprovalLevel',
        blank=True,
        related_name='visible_comments',
        help_text="Select specific approval levels that can see this comment"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.form.form_number} - Comment by {self.commented_by}"

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Approval Comments"

    def is_visible_to(self, user):
        """Check if this comment is visible to the given user"""
        if not self.commented_by:
            return False

        # Always visible to the person who wrote the comment
        if user == self.commented_by:
            return True

        # Get user level
        try:
            user_level_obj = user.approval_level_assignments.first()
            if not user_level_obj:
                return self.show_to_lower_levels
            user_level = user_level_obj.approval_level.level_number
        except:
            return self.show_to_lower_levels

        # Get commenter's level
        try:
            commenter_level_obj = self.commented_by.approval_level_assignments.first()
            if not commenter_level_obj:
                return self.show_to_lower_levels
            comment_level = commenter_level_obj.approval_level.level_number
        except:
            return self.show_to_lower_levels

        # If higher level is commenting (smaller number = higher authority)
        if comment_level < user_level:
            # Check selective visibility first
            if self.visible_to_levels.exists():
                return self.visible_to_levels.filter(level_number__gte=user_level).exists()
            # Fallback to simple checkbox
            return self.show_to_lower_levels

        # Same level or lower can always see the comment
        return True


class UserRole(models.Model):
    """User Role Assignment"""
    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("end_user", "End User"),
        ("operator", "Operator"),
        ("mk_sabhya", "MK Sabhya"),
        ("mk_sant", "MK Sant 1"),
        ("p_rajipaswami", "MK Sant 2"),
        ("hdh_guruji", "HDH Guruji"),
        ("third_party", "3rd Party Verifier"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="approval_role")
    role = models.CharField(max_length=50, choices=ROLE_CHOICES)
    
    # NEW: Many-to-Many relationships for Prabhari access
    accessible_centers = models.ManyToManyField(Center, blank=True, related_name="prabhari_roles")
    accessible_departments = models.ManyToManyField(Department, blank=True, related_name="prabhari_roles")

    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    center = models.ForeignKey(Center, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    mobile_number = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

    class Meta:
        verbose_name_plural = "User Roles"


class NotificationLog(models.Model):
    """Track all notification communication pathways cleanly (Email, SMS, WhatsApp)"""
    
    # Unified choices mapping directly to your action keys, email, and whatsapp templates
    NOTIFICATION_TYPES = [
        # --- Form Core Lifecycle ---
        ("form_submitted", "Form Successfully Submitted"),
        
        # --- Workflow Movements ---
        ("pending_approval", "Pending Approval (Forwarded to Next Level)"),
        ("revision_requested", "Revision/Resubmission Requested"),
        ("final_approved", "Final Approval Completed"),
        ("final_approved_amount_changed", "Final Approval with Amount Change"),
        ("amount_changed_notify_approvers", "Amount Changed Notification to Approvers"),
        ("rejected", "Form Permanently Rejected"),

        # --- 3rd Party Verification & Delegations ---
        ("delegated", "Internal Delegation to 3rd Party"),
        ("external_delegated", "External Delegation to Guest Email"),
        ("approved_by_internal", "3rd Party Verified (Internal)"),
        ("approved_by_external", "3rd Party Verified (External)"),
        ("rejected_by_internal", "3rd Party Rejected (Internal)"),
        ("rejected_by_external", "3rd Party Rejected (External)"),
        ("delegation_returned", "Delegation Formally Returned"),  
        ("external_delegation_reply", "External Delegation Reply Received"),

        # --- Onboarding & Security ---
        ("reg_leader_pending", "External User Pending Local Review"),
        ("reg_admin_pending", "User Verified Awaiting Admin Activation"),
        ("reg_user_activated", "User Account Activated"),
        ("reg_user_declined", "User Account Registration Declined"),
        ("new_user_registered", "Internal User Registered Awaiting Admin"),
        ("otp_sent", "Password Reset OTP Transmission"),
    ]

    CHANNEL_CHOICES = [
        ("email", "Email Message"),
        ("sms", "Cellular SMS"),
        ("whatsapp", "WhatsApp Notification"),
    ]

    STATUS_CHOICES = [
        ("sent", "Sent Successfully"),
        ("failed", "Delivery Failed"),
        ("pending", "Pending Queue"),
    ]

    form = models.ForeignKey(ApprovalForm, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    media_channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default="email")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="sent")
    
    recipient = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    recipient_email = models.EmailField(blank=True, null=True)
    recipient_phone = models.CharField(max_length=20, blank=True, null=True)
    
    subject = models.CharField(max_length=500, blank=True, null=True) # Blank for WhatsApp/SMS
    message = models.TextField(help_text="Stores the fully compiled text message or email HTML content body")
    
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Legacy fallback fields to prevent unmanaged migration crashes
    sms_status = models.CharField(max_length=20, default='not_sent', blank=True, null=True)
    sms_message_id = models.CharField(max_length=100, blank=True, null=True)
    sms_error = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"[{self.get_media_channel_display()}] {self.form.form_number} - {self.status}"

    class Meta:
        verbose_name_plural = "Notification Logs"
        ordering = ["-created_at"]


class ApprovalLevelUser(models.Model):
    """Bind multiple users to approval levels + departments"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="approval_level_assignments")
    approval_level = models.ForeignKey(ApprovalLevel, on_delete=models.CASCADE)
    departments = models.ManyToManyField(Department, help_text="Departments this user is responsible for")
    is_active = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        dept_names = ", ".join([d.name for d in self.departments.all()]) or "(No depts assigned)"
        return f"{self.user.get_full_name() or self.user.username} - {self.approval_level.level_name} - {dept_names}"

    class Meta:
        verbose_name_plural = "Approval Level Users"
        unique_together = [["user", "approval_level"]]
        ordering = ["approval_level", "user"]


class UserProfile(models.Model):
    """User Profile with Center or Department assignment"""
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='user_profile',   # CHANGED from 'profile' to 'user_profile'
        primary_key=False
    )
    
    center = models.ForeignKey('Center', on_delete=models.SET_NULL, null=True, blank=True)
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True)
    
    phone = models.CharField(
        max_length=15, 
        blank=True, 
        null=True, 
        help_text="Contact number with country code, e.g. +919876543210"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.pk is None:
            return
        if self.center and self.department:
            raise ValidationError("User can belong to either Center OR Department, not both.")
        if not self.center and not self.department:
            raise ValidationError("User must be assigned to either a Center or a Department.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Profile of {self.user.get_full_name() or self.user.username}"

    class Meta:
        verbose_name_plural = "User Profiles"


class UserWorkspace(models.Model):
    """Stores multiple department assignments for Multi-Department Leaders"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='user_workspace')
    departments = models.ManyToManyField('Department', related_name='workspace_users')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Workspace Departments for {self.user.username}"


class NotificationContact(models.Model):
    """Flexible mapping for Email + SMS notifications (including overrides/delegates)"""
    
    # Link to the approval form or general use
    form = models.ForeignKey('ApprovalForm', on_delete=models.CASCADE, 
                             null=True, blank=True, related_name='notification_contacts')
    
    # Who this contact belongs to (can be null for general/system contacts)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Contact details
    name = models.CharField(max_length=255, help_text="Full name of the contact person")
    email = models.EmailField()
    phone = models.CharField(max_length=15, blank=True, null=True, 
                             help_text="Phone number with country code e.g. +919876543210")
    
    # Extra control
    is_active = models.BooleanField(default=True)
    preferred_channel = models.CharField(
        max_length=10,
        choices=[('email', 'Email Only'), ('sms', 'SMS Only'), ('both', 'Both')],
        default='both'
    )
    notes = models.TextField(blank=True, null=True, help_text="e.g. Delegate for XYZ, Emergency contact")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Notification Contacts"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.email})"

# ====================== SMS CONFIGURATION ======================

class SMSTemplate(models.Model):
    """SMS Templates - Fully controllable from Admin mapped side-by-side with Email hierarchies"""
    
    # 💡 SYNCED: Uses the exact same unified, numbered choices as your Email Templates!
    EVENT_CHOICES = [        
        # 📁 USER ACCOUNTS & REGISTRATION SERIES (REG)
        ("reg_leader_pending", "REG-01: External User Pending Local Center Leader Review"),
        ("reg_admin_pending", "REG-02: User Verified by Center Leader Awaiting Approval of Admin"),
        ("reg_user_activated", "REG-03: User Account Activated successfully"),
        ("reg_user_declined", "REG-04: User Account Registration Declined"),
        ("new_user_registered", "REG-05: Internal New User Registered Awaiting Approval of Admin"),

        # 📁 APPLICATION BASE LIFECYCLE SERIES (FRM)
        ("form_submitted", "FRM-01: Form Successfully Submitted"),

        # 📁 ACTIVE WORKFLOW MOVEMENTS SERIES (WKF)
        ("pending_approval", "WKF-01: Pending Approval (Forwarded to Next Level)"),
        ("revision_requested", "WKF-02: Revision/Resubmission Requested"),
        ("final_approved", "WKF-03: Final Approval Completed"),
        ("final_approved_amount_changed", "WKF-04: Final Approval with Amount Change (To Submitter)"),
        ("amount_changed_notify_approvers", "WKF-05: Amount Changed Notification (To Previous Approvers)"),
        ("rejected", "WKF-06: Form Permanently Rejected"),

        # 📁 WORKFLOW DELEGATION EXTENSIONS SERIES (DLG)
        ("delegated", "DLG-01: Internal Delegation to 3rd Party"),
        ("external_delegated", "DLG-02: External Delegation to Guest Email"),
        ("approved_by_internal", "DLG-03: 3rd Party Verified (Internal)"),
        ("approved_by_external", "DLG-04: 3rd Party Verified (External)"),
        ("rejected_by_internal", "DLG-05: 3rd Party Rejected (Internal)"),
        ("rejected_by_external", "DLG-06: 3rd Party Rejected (External)"),
        ("delegation_returned", "DLG-07: Delegation Formally Returned"),  
        ("external_delegation_reply", "DLG-08: External Delegation Reply Received"),

        # 🔒 SECURITY & SYSTEM CORE SERIES (SYS)
        ("otp_sent", "SYS-01: Password Reset OTP Transmission"),
    ]

    template_name = models.CharField(
        max_length=255, 
        unique=True, 
        help_text="Clear descriptive name matching Email layout (e.g., '[REG-01] Vasna Center SMS Version')"
    )

    # 💡 FIXED: Removed unique limitations from this column so multiple layout rules can map to one signal trigger
    event_type = models.CharField(max_length=50, choices=EVENT_CHOICES)
    
    # Approval Level - Optional (All Levels if blank)
    approval_level = models.ForeignKey(
        'ApprovalLevel', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Leave blank for All Levels, or select specific level"
    )
    
    message_text = models.CharField(
        max_length=160,
        help_text="SMS text (max 160 chars). Use {form_number}, {subject}, {amount}, {approver_name}, {remarks}"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        level = self.approval_level.level_name if self.approval_level else "All Levels"
        return f"{self.template_name} ({level})"

    class Meta:
        verbose_name = "SMS Template"
        verbose_name_plural = "SMS Templates"
        ordering = ["event_type"]
        
        # 💡 SAFETY FOOTPRINT: Permits custom variants, but stops identical name clumps from colliding at the same level
        unique_together = [['template_name', 'event_type', 'approval_level']]


# ==================== REPORT SYSTEM ====================

class ReportPermission(models.Model):
    """Controls who can view the Approval Report"""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='report_permission'
    )
    can_view_report = models.BooleanField(
        default=False,
        help_text="Allow this user to view the Center/Department Approval Report"
    )
    # Optionally restrict to specific centers/departments
    restrict_to_centers = models.ManyToManyField(
        Center,
        blank=True,
        help_text="Leave empty to allow all centers. Select specific centers to restrict."
    )
    restrict_to_departments = models.ManyToManyField(
        Department,
        blank=True,
        help_text="Leave empty to allow all departments. Select specific to restrict."
    )
    can_enter_actual_amount = models.BooleanField(
        default=False,
        help_text="Allow this user to fill in the Actual Amount Spent for approved forms"
    )
    can_enter_work_completion = models.BooleanField(
        default=False,
        help_text="Allow this user to enter the Work Completion % for approved forms"
    )
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} — Report Access"

    class Meta:
        verbose_name = "Report Permission"
        verbose_name_plural = "Report Permissions"
        ordering = ['user__username']


class ActualExpenditure(models.Model):
    """Records actual amount spent against an approved form"""
    form = models.OneToOneField(
        ApprovalForm,
        on_delete=models.CASCADE,
        related_name='actual_expenditure'
    )
    actual_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Actual amount spent by Center/Department"
    )
    entered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='expenditure_entries'
    )
    remarks = models.TextField(
        blank=True,
        null=True,
        help_text="Notes or context about the actual spend"
    )
    work_completion_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Completion of work in percentage (0–100)"
    )
    entered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.form.form_number} — Actual: ₹{self.actual_amount}"

    @property
    def approved_amount(self):
        return self.form.approved_amount or self.form.amount

    @property
    def difference(self):
        return self.approved_amount - self.actual_amount

    class Meta:
        verbose_name = "Actual Expenditure"
        verbose_name_plural = "Actual Expenditures"
        ordering = ['-entered_at']


class RegistrationWorkflowConfig(models.Model):
    """Dynamic configuration to manage the registration authorization flow from Admin Panel"""
    
    config_name = models.CharField(
        max_length=100, 
        default="Default Registration Rules",
        help_text="Name of this configuration block"
    )
    is_direct_registration = models.BooleanField(
        default=False,
        help_text="If checked, registrations skip all leader verification and go directly to Admin Activation status."
    )
    authorized_center_posts = models.ManyToManyField(
        PostMaster,
        blank=True,
        limit_choices_to={'post_type': 'center'},
        related_name="authorizing_center_configs",
        help_text="Select which Center Post Roles are authorized to verify center registrations (e.g., Center Sant Email ID)."
    )
    authorized_department_posts = models.ManyToManyField(
        PostMaster,
        blank=True,
        limit_choices_to={'post_type': 'department'},
        related_name="authorizing_dept_configs",
        help_text="Select which Department Post Roles are authorized to verify department registrations (e.g., Department Leader Sant Email ID)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only one configuration block should be active at a time."
    )
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        """Ensure only one active configuration exists"""
        if self.is_active:
            qs = RegistrationWorkflowConfig.objects.filter(is_active=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError("Only one Registration Workflow Configuration can be active at a time.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.config_name} — {'[DIRECT]' if self.is_direct_registration else '[VERIFIED CHAIN]'}"

    class Meta:
        verbose_name = "Registration Workflow Config"
        verbose_name_plural = "Registration Workflow Configs"


class BackupRestoreLog(models.Model):
    ACTION_CHOICES = [
        ('backup_full', 'Full Project Backup'),
        ('backup_db', 'Database Only Backup'),
        ('restore_full', 'Full Project Restore'),
        ('restore_db', 'Database Only Restore'),
    ]
    
    action_type = models.CharField(max_length=20, choices=ACTION_CHOICES)
    filename = models.CharField(max_length=255)
    file_size_kb = models.DecimalField(max_length=20, max_digits=12, decimal_places=2, null=True, blank=True)
    executed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='pending') # success, failed
    
    # Data Integrity Verification Metrics
    user_count_verified = models.IntegerField(default=0)
    form_count_verified = models.IntegerField(default=0)
    log_summary = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_action_type_display()} - {self.status} ({self.created_at.strftime('%d-%m-%Y %H:%M')})"


class WhatsAppNotificationTemplate(models.Model):
    """Dynamic WhatsApp templates editable by admin with variable substitution support"""

    # 💡 SYNCED: Uses the exact same unified event choices as your Email Templates!
    EVENT_TYPE_CHOICES = [
        # 📁 USER ACCOUNTS & REGISTRATION SERIES (REG)
        ("reg_leader_pending", "REG-01: External User Pending Local Center Leader Review"),
        ("reg_admin_pending", "REG-02: User Verified by Center Leader Awaiting Approval of Admin"),
        ("reg_user_activated", "REG-03: User Account Activated successfully"),
        ("reg_user_declined", "REG-04: User Account Registration Declined"),
        ("new_user_registered", "REG-05: Internal New User Registered Awaiting Approval of Admin"),

        # 📁 APPLICATION BASE LIFECYCLE SERIES (FRM)
        ("form_submitted", "FRM-01: Form Successfully Submitted"),

        # 📁 ACTIVE WORKFLOW MOVEMENTS SERIES (WKF)
        ("pending_approval", "WKF-01: Pending Approval (Forwarded to Next Level)"),
        ("revision_requested", "WKF-02: Revision/Resubmission Requested"),
        ("final_approved", "WKF-03: Final Approval Completed"),
        ("final_approved_amount_changed", "WKF-04: Final Approval with Amount Change (To Submitter)"),
        ("amount_changed_notify_approvers", "WKF-05: Amount Changed Notification (To Previous Approvers)"),
        ("rejected", "WKF-06: Form Permanently Rejected"),

        # 📁 WORKFLOW DELEGATION EXTENSIONS SERIES (DLG)
        ("delegated", "DLG-01: Internal Delegation to 3rd Party"),
        ("external_delegated", "DLG-02: External Delegation to Guest Email"),
        ("approved_by_internal", "DLG-03: 3rd Party Verified (Internal)"),
        ("approved_by_external", "DLG-04: 3rd Party Verified (External)"),
        ("rejected_by_internal", "DLG-05: 3rd Party Rejected (Internal)"),
        ("rejected_by_external", "DLG-06: 3rd Party Rejected (External)"),
        ("delegation_returned", "DLG-07: Delegation Formally Returned"),  
        ("external_delegation_reply", "DLG-08: External Delegation Reply Received"),

        # 🔒 SECURITY & SYSTEM CORE SERIES (SYS)
        ("otp_sent", "SYS-01: Password Reset OTP Transmission"),
    ]

    template_name = models.CharField(max_length=255, unique=True, help_text="Internal descriptive name, e.g., '[WKF-01] Forwarded WhatsApp alert'")
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)
    
    # Pinbot.ai requires template string mapping
    message_body = models.TextField(help_text="Use Django bracket placeholders: {form_number}, {subject}, {amount}, {approver_name}, {remarks}")
    
    # Button configuration lines for Pinbot interactive template rules
    button_text = models.CharField(max_length=100, blank=True, null=True, help_text="Optional text for CTA template buttons")
    button_url = models.CharField(max_length=500, blank=True, null=True, help_text="Optional URL route for interactive redirect cards")

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.template_name} - {self.get_event_type_display()}"

    class Meta:
        verbose_name = "WhatsApp Notification Template"
        verbose_name_plural = "WhatsApp Notification Templates"
        ordering = ["event_type"]
        unique_together = (('template_name', 'event_type'),)


class NotificationRoutingMatrix(models.Model):
    EVENT_CHOICES = [
        # --- Form & Core Workflow Events ---
        ('FRM-01', 'FRM-01: Form Successfully Submitted'),
        ('WKF-01', 'WKF-01: Pending Approval (Forwarded to Next Level)'),
        ('WKF-02', 'WKF-02: Revision/Resubmission Requested'),
        ('WKF-03', 'WKF-03: Final Approval Completed'),
        ('WKF-04', 'WKF-04: Final Approval with Amount Change (To Submitter)'),
        ('WKF-05', 'WKF-05: Amount Changed Notification (To Previous Approvers)'),
        ('WKF-06', 'WKF-06: Form Permanently Rejected'),
        
        # --- 3rd Party Delegation Events ---
        ('DLG-01', 'DLG-01: Internal Delegation to 3rd Party'),
        ('DLG-02', 'DLG-02: External Delegation to Guest Email'),
        ('DLG-03', 'DLG-03: 3rd Party Verified (Internal)'),
        ('DLG-04', 'DLG-04: 3rd Party Verified (External)'),
        ('DLG-05', 'DLG-05: 3rd Party Rejected (Internal)'),
        ('DLG-06', 'DLG-06: 3rd Party Rejected (External)'),
        ('DLG-07', 'DLG-07: Delegation Formally Returned'),
        ('DLG-08', 'DLG-08: External Delegation Reply Received'),
      
        # --- User Account & Security Events ---
        ('REG-01', 'REG-01: External User Pending Local Center Leader Review'),
        ('REG-02', 'REG-02: User Verified by Center Leader Awaiting Approval of Admin'),
        ('REG-03', 'REG-03: User Account Activated successfully'),
        ('REG-04', 'REG-04: User Account Registration Declined'),
        ('REG-05', 'REG-05: Internal New User Registered Awaiting Approval of Admin'),
        ('SYS-01', 'SYS-01: Password Reset OTP Transmission'),
        ('user_act', 'user_activated: User Account Activation - To User'),
    ]

    event_type = models.CharField(
        max_length=10, 
        choices=EVENT_CHOICES, 
        unique=True, 
        verbose_name="System Event Type"
    )

    # ==========================================================
    # EMAIL CHANNEL MATRIX (Checkboxes based on Post Master)
    # ==========================================================
    # Center Posts (Email)
    email_center_accountant = models.BooleanField(default=False, verbose_name="Email: Center Accountant")
    email_center = models.BooleanField(default=False, verbose_name="Email: Center")
    email_center_sant = models.BooleanField(default=False, verbose_name="Email: Center Sant")
    email_prabhari_sant = models.BooleanField(default=False, verbose_name="Email: Prabhari Sant")
    email_zonal_head = models.BooleanField(default=False, verbose_name="Email: Zonal Head")
    
    # Department Posts (Email)
    email_department = models.BooleanField(default=False, verbose_name="Email: Department")
    email_dept_leader_sant = models.BooleanField(default=False, verbose_name="Email: Dept Leader Sant")
    email_dept_sant = models.BooleanField(default=False, verbose_name="Email: Dept Sant")
    email_hod = models.BooleanField(default=False, verbose_name="Email: HOD")
    email_mk_haribhakt = models.BooleanField(default=False, verbose_name="Email: MK Haribhakt")
    email_mk_sant = models.BooleanField(default=False, verbose_name="Email: MK Sant")
    email_secretary = models.BooleanField(default=False, verbose_name="Email: Secretary")
    email_end_user = models.BooleanField(default=False, verbose_name="Email: End User (Submitter)")

    # ==========================================================
    # WHATSAPP CHANNEL MATRIX (Checkboxes based on Post Master)
    # ==========================================================
    # Center Posts (WhatsApp)
    wa_center_accountant = models.BooleanField(default=False, verbose_name="WA: Center Accountant")
    wa_center = models.BooleanField(default=False, verbose_name="WA: Center")
    wa_center_sant = models.BooleanField(default=False, verbose_name="WA: Center Sant")
    wa_prabhari_sant = models.BooleanField(default=False, verbose_name="WA: Prabhari Sant")
    wa_zonal_head = models.BooleanField(default=False, verbose_name="WA: Zonal Head")

    # Department Posts (WhatsApp)
    wa_department = models.BooleanField(default=False, verbose_name="WA: Department")
    wa_dept_leader_sant = models.BooleanField(default=False, verbose_name="WA: Dept Leader Sant")
    wa_dept_sant = models.BooleanField(default=False, verbose_name="WA: Dept Sant")
    wa_hod = models.BooleanField(default=False, verbose_name="WA: HOD")
    wa_mk_haribhakt = models.BooleanField(default=False, verbose_name="WA: MK Haribhakt")
    wa_mk_sant = models.BooleanField(default=False, verbose_name="WA: MK Sant")
    wa_secretary = models.BooleanField(default=False, verbose_name="WA: Secretary")
    wa_end_user = models.BooleanField(default=False, verbose_name="WA: End User (Submitter)")

    # ==========================================================
    # SMS CHANNEL MATRIX (Checkboxes based on Post Master)
    # ==========================================================

    sms_center_accountant = models.BooleanField(default=False, verbose_name="SMS: Center Accountant")
    sms_center = models.BooleanField(default=False, verbose_name="SMS: Center")
    sms_center_sant = models.BooleanField(default=False, verbose_name="SMS: Center Sant")
    sms_prabhari_sant = models.BooleanField(default=False, verbose_name="SMS: Prabhari Sant")
    sms_zonal_head = models.BooleanField(default=False, verbose_name="SMS: Zonal Head")
    
    sms_department = models.BooleanField(default=False, verbose_name="SMS: Department")
    sms_dept_leader_sant = models.BooleanField(default=False, verbose_name="SMS: Dept Leader Sant")
    sms_dept_sant = models.BooleanField(default=False, verbose_name="SMS: Dept Sant")
    sms_hod = models.BooleanField(default=False, verbose_name="SMS: HOD")
    sms_mk_haribhakt = models.BooleanField(default=False, verbose_name="SMS: MK Haribhakt")
    sms_mk_sant = models.BooleanField(default=False, verbose_name="SMS: MK Sant")
    sms_secretary = models.BooleanField(default=False, verbose_name="SMS: Secretary")
    sms_end_user = models.BooleanField(default=False, verbose_name="SMS: End User (Submitter)")

    class Meta:
        verbose_name = "Unified Notification Router"
        verbose_name_plural = "Unified Notification Router"

    def __str__(self):
        return self.get_event_type_display()