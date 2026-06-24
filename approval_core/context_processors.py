from approval_core.models import Department

def nav_permissions(request):
    """
    Returns a dict available in every template:

        {{ can_view_report }}          — True/False → show/hide Report menu item
        {{ can_enter_actual }}         — True/False → show Enter Actual Amount button
        {{ can_enter_work_completion}} — True/False → show Completion of Work % field
        {{ nav_user_role }}            — the UserRole object for the logged-in user (or None)
    """
    ctx = {
        'can_view_report': False,
        'can_enter_actual': False,
        'can_enter_work_completion': False,
        'nav_user_role': None,
    }

    if not request.user.is_authenticated:
        return ctx

    # Superuser always gets full access
    if request.user.is_superuser:
        ctx['can_view_report'] = True
        ctx['can_enter_actual'] = True
        ctx['can_enter_work_completion'] = True
    else:
        try:
            perm = request.user.report_permission
            ctx['can_view_report'] = perm.can_view_report
            ctx['can_enter_actual'] = perm.can_enter_actual_amount
            ctx['can_enter_work_completion'] = perm.can_enter_work_completion
        except Exception:
            pass  # No ReportPermission record = no access

    # Expose role for nav rendering (Admin panel link, etc.)
    try:
        ctx['nav_user_role'] = request.user.approval_role
    except Exception:
        ctx['nav_user_role'] = None

    return ctx


def active_workspace_processor(request):
    """
    Globally exposes the active workspace department object to all templates
    extended by base.html, eliminating hardcoded HTML mappings.
    """
    context = {
        'active_workspace_dept': None
    }
    
    if request.user.is_authenticated:
        active_dept_id = request.session.get('active_workspace_dept_id')
        if active_dept_id:
            try:
                # Fetch the full active department database row item dynamically
                context['active_workspace_dept'] = Department.objects.get(id=active_dept_id)
            except Department.DoesNotExist:
                pass
                
    return context