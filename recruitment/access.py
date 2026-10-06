"""
recruitment/access.py

Who may join a candidate's live chat. The chat shows the same stage notes the
pipeline pages show to staff, and those pages require ``recruitment.view_stagenote``
or being a stage / recruitment / onboarding manager (see ``all_manager_can_enter``
in recruitment/decorators.py). The websocket used to accept *any* logged-in
employee, so it is held to the same rule.
"""

CHAT_PERMISSION = "recruitment.view_stagenote"


def staff_can_use_chat(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.has_perm(CHAT_PERMISSION):
        return True

    from employee.models import Employee
    from recruitment.models import Recruitment, Stage

    employee = Employee.objects.filter(employee_user_id=user).first()
    if employee is None:
        return False
    return bool(
        Stage.objects.filter(stage_managers=employee).exists()
        or Recruitment.objects.filter(recruitment_managers=employee).exists()
        or employee.onboardingstage_set.exists()
        or employee.onboarding_task.exists()
    )
