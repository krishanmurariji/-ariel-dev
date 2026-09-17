"""
recruitmentfilters.py

This module is used to write custom template filters.

"""

import json
import uuid

from django import template
from django.apps import apps
from django.template.defaultfilters import register
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from horilla_auth.models import HorillaUser

# from django.forms.boundfield

register = template.Library()


@register.filter(name="is_stagemanager")
def is_stagemanager(user):
    """
    This method is used to check the employee is stage or recruitment manager
    """
    try:
        employee_obj = user.employee_get
        return (
            employee_obj.stage_set.all().exists()
            or employee_obj.recruitment_set.exists()
        )
    except Exception:
        return False


@register.filter(name="is_any_manager")
def is_any_manager(request):
    """
    This method is used to check the employee is stage or recruitment manager
    """
    return (
        request.user.employee_get.stage_set.exists()
        or request.user.employee_get.recruitment_set.exists()
        or request.user.employee_get.onboardingstage_set.exists()
        or request.user.employee_get.onboarding_task.exists()
    )


@register.filter(name="is_recruitmentmanager")
def is_recruitmentmangers(user):
    """
    This method is used to check the employee is recruitment manager
    """
    try:
        employee_obj = user.employee_get
        return employee_obj.recruitment_set.exists()
    except Exception:
        return False


@register.filter(name="stage_manages")
def stage_manages(user, recruitment):
    """
    This method is used to check the employee is manager in any stage in a recruitment
    """
    try:
        return (
            recruitment.stage_set.filter(stage_managers=user.employee_get).exists()
            or recruitment.recruitment_managers.filter(id=user.employee_get.id).exists()
        )
    except Exception as _:
        return False


@register.filter(name="recruitment_manages")
def recruitment_manages(user, recruitment):
    """
    This method is used to check the employee in recruitment managers
    """
    try:
        employee_obj = user.employee_get
        return recruitment.recruitment_managers.filter(id=employee_obj.id).exists()
    except Exception:
        return False


@register.filter(name="employee")
def employee(uid):
    """
    This method is used to return user object with the arg id.

    Args:
        uid (int): user object id

    Returns:
        user object
    """
    return HorillaUser.objects.get(id=uid).employee_get if uid is not None else None


@register.filter(name="media_path")
def media_path(form_tag):
    """
    This method will returns the path of media
    """
    return form_tag.subwidgets[0].__dict__["data"]["value"]


@register.filter(name="generate_id")
def generate_id(element, label=""):
    """
    This method is used to generate element id attr
    """
    element.field.widget.attrs.update({"id": label + str(uuid.uuid1())})
    return element


def _find_rating_for_candidate(candidate_ratings, cand_id):
    """
    candidate_ratings is a queryset the caller already has in hand (e.g.
    request.user.employee_get.candidate_rating.all or
    instance.candidate_rating.all), shared across every row on a list/kanban
    page via one {% with %} block. Calling .filter() on it here would clone
    it into a brand new queryset and issue a fresh query every single call -
    once per row, sometimes once per star (5x/row). Iterating it in plain
    Python instead means the first iteration populates Django's own
    queryset result cache, and every call after that (for every other row
    on the page) reads from that cache with no further queries at all.
    """
    for candidate_rating in candidate_ratings:
        if candidate_rating.candidate_id_id == cand_id:
            return candidate_rating
    return None


@register.filter(name="has_candidate_rating")
def has_candidate_rating(candidate_ratings, cand):
    return _find_rating_for_candidate(candidate_ratings, cand.id)


@register.filter(name="rating")
def rating(candidate_ratings, cand):
    found = _find_rating_for_candidate(candidate_ratings, cand.id)
    return str(found.rating) if found else ""


@register.filter(name="avg_rating")
def avg_rating(candidate_ratings, cand):
    """
    candidate_ratings is expected to be cand's own candidate_rating.all
    (every rating this specific candidate has received, from any
    employee) - unlike the two filters above, there's no per-candidate
    filtering left to do here, so this just averages whatever was passed
    in instead of re-querying CandidateRating from scratch, which is what
    it used to do (ignoring candidate_ratings entirely and issuing a fresh
    query - previously the single biggest cost on the candidate list page,
    since it runs once per row and used to double as a second, redundant
    query on top of evaluating candidate_ratings in the first place).
    """
    rating_list = [candidate_rating.rating for candidate_rating in candidate_ratings]
    avg_rate = round(sum(rating_list) / len(rating_list)) if rating_list else 0
    return str(avg_rate)


@register.filter(name="percentage")
def percentage(value, total):
    if total == 0 or not total:
        return 0
    return min(round((value / total) * 100, 2), 100)


@register.filter(name="is_in_task_managers")
def is_in_task_managers(user):
    """
    This method is used to check the user in the task manager or not
    """
    if apps.is_installed("onboarding"):
        from onboarding.models import OnboardingTask

        return OnboardingTask.objects.filter(
            employee_id__employee_user_id=user
        ).exists()
    return False


@register.filter(name="pipeline_grouper")
def pipeline_grouper(grouper: dict = {}):
    """
    This method is used itemize the dictionary
    """
    return grouper["title"], grouper["stages"]


@register.filter(name="to_json")
def to_json(value):
    ordered_list = [
        {"id": val.id, "stage": val.stage, "type": val.stage_type} for val in value
    ]
    return json.dumps(ordered_list)


@register.filter(name="chat_date_label")
def chat_date_label(value):
    """
    WhatsApp-style day divider label for a chat message's created_at -
    "Today"/"Yesterday" for the last two days, otherwise a plain date like
    "01 Aug 2026". Used with {% ifchanged %} in the chat templates to group
    messages by day.
    """
    if not value:
        return ""
    message_date = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    today = timezone.localdate()
    delta = (today - message_date).days
    if delta == 0:
        return _("Today")
    if delta == 1:
        return _("Yesterday")
    return date_format(message_date, "d M Y")
