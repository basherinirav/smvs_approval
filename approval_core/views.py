from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .forms import ApprovalFormCreationForm, ApprovalDocumentForm
from .models import ApprovalForm, ApprovalDocument
from django.contrib import messages

import uuid


@login_required
def create_approval_form(request):
    if request.method == "POST":
        form = ApprovalFormCreationForm(request.POST)

        if form.is_valid():
            approval_form = form.save(commit=False)
            approval_form.submitted_by = request.user
            approval_form.form_number = f"FORM-{uuid.uuid4().hex[:8].upper()}"
            approval_form.status = "initiated"
            approval_form.save()

            return redirect("form_detail", form_id=approval_form.id)

    else:
        form = ApprovalFormCreationForm()

    return render(request, "approval_core/create_form.html", {"form": form})


@login_required
def upload_document(request, form_id):
    approval_form = ApprovalForm.objects.get(id=form_id)

    if request.method == "POST":
        form = ApprovalDocumentForm(request.POST, request.FILES)

        if form.is_valid():
            doc = form.save(commit=False)
            doc.form = approval_form
            doc.uploaded_by = request.user
            doc.save()

            messages.success(request, "Document uploaded successfully ✅")

    return redirect("form_detail", form_id=form_id)

@login_required
def add_comment(request, form_id):
    approval_form = get_object_or_404(ApprovalForm, id=form_id)

    if request.method == "POST":
        comment_text = request.POST.get('comment_text')
        show_to_lower = request.POST.get('show_to_lower_levels') == '1'

        if comment_text:
            ApprovalComment.objects.create(
                form=approval_form,
                commented_by=request.user,
                comment_text=comment_text,
                show_to_lower_levels=show_to_lower
            )
            messages.success(request, "Comment posted successfully.")

    return redirect("form_detail", form_id=form_id)