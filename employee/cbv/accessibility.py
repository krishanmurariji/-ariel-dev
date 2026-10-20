"""
Accessiblility
"""

from django.contrib.auth.context_processors import PermWrapper

from base.methods import check_manager
from employee.models import Employee
from horilla_audit.models import AccountBlockUnblock


def second_manager_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """HR (who may change work information) sees "Second Manager" on someone who is still here."""
    return (
        instance is not None
        and instance.is_active
        and request.user.has_perm("employee.change_employeeworkinformation")
    )


def initiate_exit_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    Show "Initiate Exit" to people who run exits, on someone who is still
    here and not already leaving.
    """
    from django.apps import apps

    if not apps.is_installed("offboarding") or instance is None:
        return False
    if not request.user.has_perm("offboarding.change_offboardingemployee"):
        return False
    from offboarding.models import OffboardingEmployee

    return instance.is_active and not OffboardingEmployee.objects.entire().filter(
        employee_id=instance, status__in=["in_progress", "exited"]
    ).exists()


def edit_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    To access edit
    """
    # employee = Employee.objects.get(id=instance.pk)
    if (
        (request.user.has_perm("employee.change_employee"))
        or check_manager(request.user.employee_get, instance)
        or request.user == instance.employee_user_id
    ):
        return True
    return False


def password_reset_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    To password  reset
    """
    # employee = Employee.objects.get(id=instance.pk)
    if (
        (request.user.has_perm("employee.add_employee"))
        or check_manager(request.user.employee_get, instance)
        or request.user == instance.employee_user_id
    ):
        return True
    return False


def block_account_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    To block  account
    """
    enabled_block_unblock = (
        AccountBlockUnblock.objects.exists()
        and AccountBlockUnblock.objects.first().is_enabled
    )
    if (
        enabled_block_unblock
        and request.user.has_perm("employee.change_employee")
        and instance.employee_user_id.is_active
    ):
        return True
    return False


def un_block_account_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    To block  account
    """
    enabled_block_unblock = (
        AccountBlockUnblock.objects.exists()
        and AccountBlockUnblock.objects.first().is_enabled
    )
    if (
        enabled_block_unblock
        and request.user.has_perm("employee.change_employee")
        and not instance.employee_user_id.is_active
    ):
        return True
    return False


def action_accessible(request, instance, user_perms):
    """
    To access archive and delete functionalities

    """

    if request.user.has_perm("employee.change_employee"):
        return True


def permission_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    accessibility for permissions tab in employee profile and individual view
    """
    if request.user.has_perm("auth.view_permissions") or request.user.has_perm(
        "auth.view_group"
    ):
        return True
    return False


def note_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    accessibility for note tab
    """
    if instance.employee_user_id != request.user or request.user.is_superuser:
        if request.user.has_perm("employee.view_employeenote") or check_manager(
            request.user.employee_get, instance
        ):
            return True
    return False


def document_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    accessibility for document tab
    """
    employee = Employee.objects.get(id=instance.pk)
    if (
        request.user.has_perm("horilla_documents.view_document")
        or request.user == employee.employee_user_id
    ):
        return True
    return False


def workshift_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    permission for work type and shift tab in employee profile
    """
    employee = Employee.objects.get(id=instance.pk)
    check_manages = check_manager(request.user.employee_get, instance)
    if (
        request.user == employee.employee_user_id
        or check_manages
        or request.user.has_perm("base.view_worktyperequest")
        or request.user.has_perm("base.view_shiftrequest")
    ):
        return True
    return False


def mail_log_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    permission for mail log tab
    """

    if request.user.has_perm("employee.view_employee"):
        return True
    return False


def history_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    accessibility for history tab
    """
    if request.user.has_perm(
        "employee.view_historicalemployeeworkinformation"
    ) or check_manager(request.user.employee_get, instance):
        return True
    return False


def project_accessibility(
    request, instance: object = None, user_perms: PermWrapper = [], *args, **kwargs
) -> bool:
    """
    permission for work type and shift tab in employee profile
    """
    employee = Employee.objects.get(id=instance.pk)
    check_manages = check_manager(request.user.employee_get, instance)
    if (
        request.user == employee.employee_user_id
        or check_manages
        or request.user.has_perm("project.view_project")
    ):
        return True
    return False
