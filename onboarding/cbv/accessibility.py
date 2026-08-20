"""
onboarding/cbv/accessibility.py
"""

from onboarding.templatetags.onboardingfilters import stage_manages
from recruitment.methods import recruitment_manages


def edit_stage_accessibility(
    request, instance=None, user_perms=[], *args, **kwargs
) -> bool:
    """
    Edit accessibility
    """
    return request.user.has_perm("onboarding.change_onboardingstage") or stage_manages(
        request.user, instance
    )


def delete_stage_accessibility(
    request, instance=None, user_perms=[], *args, **kwargs
) -> bool:
    """
    Delete accessibility
    """
    return request.user.has_perm(
        "onboarding.delete_onboardingstage"
    ) or recruitment_manages(request.user, instance.recruitment_id)


def add_task_accessibility(
    request, instance=None, user_perms=[], *args, **kwargs
) -> bool:
    """
    Add Task accessibility - only meaningful once the stage actually has
    a candidate to assign the task to, so the action is hidden for empty
    stages rather than opening a form with nothing to select.
    """
    return bool(instance) and instance.candidate.exists()
