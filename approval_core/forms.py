from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import ApprovalForm, ApprovalDocument, ApprovalComment, ApprovalAction

class EndUserRegistrationForm(UserCreationForm):
    """Registration form for End Users"""
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    mobile_number = forms.CharField(max_length=15, required=True, label="Mobile Number")
 
    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered.")
        return email


class ApprovalFormCreationForm(forms.ModelForm):
    """Form for End Users to create approval requests"""
    
    class Meta:
        model = ApprovalForm
        fields = ("subject", "description", "amount")
        widgets = {
            "subject": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Enter subject",
                "maxlength": "500",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Enter detailed description",
                "rows": 6,
            }),
            "amount": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "Enter amount",
                "min": "0",
                "step": "0.01",
            }),
        }


class ApprovalDocumentForm(forms.ModelForm):
    """Form for uploading documents"""
    
    class Meta:
        model = ApprovalDocument
        fields = ("document_type", "file")
        widgets = {
            "document_type": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., Invoice, Quotation, Receipt",
            }),
            "file": forms.FileInput(attrs={
                "class": "form-control",
                "accept": ".pdf",
            }),
        }


class ApprovalActionForm(forms.ModelForm):
    """Form for operators/approvers to take action on forms"""
    
    class Meta:
        model = ApprovalAction
        fields = ("action_type", "remarks", "delegated_to")
        widgets = {
            "action_type": forms.Select(attrs={
                "class": "form-control",
            }),
            "remarks": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Enter remarks or reasons",
            }),
            "delegated_to": forms.Select(attrs={
                "class": "form-control",
            }),
        }


class ApprovalCommentForm(forms.ModelForm):
    """Form for adding comments/messages"""
    
    class Meta:
        model = ApprovalComment
        fields = ("comment_text",)
        widgets = {
            "comment_text": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Enter your comment/message",
            }),
        }


class ApprovalFilterForm(forms.Form):
    """Form for filtering approval applications"""
    STATUS_CHOICES = [("", "All Statuses")] + ApprovalForm.STATUS_CHOICES
    DEPARTMENT_CHOICES = []
    
    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"})
    )
    
    department = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Filter by department",
        })
    )
    
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            "class": "form-control",
            "type": "date",
        })
    )
    
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            "class": "form-control",
            "type": "date",
        })
    )
    
    amount_from = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "Amount from",
            "min": "0",
        })
    )
    
    amount_to = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "placeholder": "Amount to",
            "min": "0",
        })
    )


class RevisionUploadForm(forms.Form):
    """Form for End Users to upload revised documents"""
    document_type = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Document type (e.g., Revised Invoice)",
        })
    )
    
    file = forms.FileField(
        widget=forms.FileInput(attrs={
            "class": "form-control",
            "accept": ".pdf",
        })
    )
    
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Optional notes about the revision",
        })
    )

def clean_attachment(self):
    file = self.cleaned_data.get("attachment")

    if file:
        if not file.name.endswith(".pdf"):
            raise forms.ValidationError("Only PDF files are allowed")

    return file