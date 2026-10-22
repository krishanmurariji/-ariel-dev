"""
offboarding/exit_settings.py

The "Exit Settings" page (Settings > Offboarding): resignation and
termination rules, the list of exit reasons, and named notice period
policies with who is on which.

Every action works two ways. From the page it is an htmx call: the view
answers with no body and a header that makes the page pop up a confirmation,
close the open window and refresh the affected section, so nothing reloads.
A plain form post (no JavaScript, or a script posting directly) still works:
it adds a message and returns to the page.

Scope: with a company selected the page edits that company's rows; in "All
Company" mode it edits the organisation-wide ones. Organisation-wide reasons
and policies still show (read-only) inside a company, since they apply there
too.
"""

import json

from django import forms
from django.contrib import messages
from django.db.models import Count, ProtectedError, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from employee.models import Employee
from horilla.decorators import login_required, permission_required
from offboarding.models import (
    EmployeeNoticePolicy,
    ExitReasonOption,
    NoticePeriodPolicy,
    OffboardingGeneralSetting,
)

PERM = "offboarding.change_offboardinggeneralsetting"


def _scope(request):
    """The company id being edited, or None for organisation-wide."""
    selected = request.session.get("selected_company")
    return int(selected) if selected and str(selected).isdigit() else None


def _in_scope(model, request):
    scope = _scope(request)
    rows = model.objects.entire()
    return rows.filter(company_id=scope) if scope else rows.filter(company_id__isnull=True)


def _visible(model, request):
    """Rows shown on the page: this company's plus the organisation-wide ones."""
    scope = _scope(request)
    rows = model.objects.entire()
    if scope:
        return rows.filter(Q(company_id=scope) | Q(company_id__isnull=True))
    return rows.filter(company_id__isnull=True)


def _back(name="exit-settings"):
    return redirect(name)


def _errors(form):
    return "; ".join(
        f"{error}" for errors in form.errors.values() for error in errors
    )


def _htmx(request):
    return request.headers.get("HX-Request") == "true"


def _answer(request, message=None, error=None, body=None, close=True, back="exit-settings"):
    """
    Finish an action. The result is a normal Django message, so it shows in the
    application's own pop-up. From htmx: send `body` (or nothing) and tell the
    page to show the message, close the open window and refresh the sections.
    From a plain post: go back to the page, where the message is shown.
    """
    if message:
        messages.success(request, message)
    else:
        messages.error(request, error)
    if not _htmx(request):
        return _back(back)
    response = body if body is not None else HttpResponse(status=204)
    events = {"exitSettingsNotice": {"close": bool(close and message)}}
    if message:
        events["exitSettingsChanged"] = True
    response["HX-Trigger"] = json.dumps(events)
    return response


class GeneralSettingsForm(forms.ModelForm):
    class Meta:
        model = OffboardingGeneralSetting
        fields = [
            "resignation_request",
            "allow_withdrawal",
            "allow_early_last_working_day",
            "show_approval_status",
            "review_mode",
            "notice_starts_on_resignation_date",
            "last_working_day_holiday_rule",
        ]
        widgets = {
            "review_mode": forms.RadioSelect,
            "last_working_day_holiday_rule": forms.Select(attrs={"class": "oh-select w-100"}),
        }


class ReasonForm(forms.ModelForm):
    class Meta:
        model = ExitReasonOption
        fields = ["title", "exit_type"]

    def clean_title(self):
        return (self.cleaned_data["title"] or "").strip()


class PolicyForm(forms.ModelForm):
    class Meta:
        model = NoticePeriodPolicy
        fields = ["title", "description", "days", "is_default"]

    def clean_title(self):
        return (self.cleaned_data["title"] or "").strip()


class AssignForm(forms.Form):
    policy = forms.ModelChoiceField(
        queryset=NoticePeriodPolicy.objects.none(), required=False
    )
    employees = forms.ModelMultipleChoiceField(queryset=Employee.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["policy"].queryset = NoticePeriodPolicy.objects.entire()
        self.fields["employees"].queryset = Employee.objects.filter(is_active=True)


# --------------------------------------------------------------------------
# the page and its sections
# --------------------------------------------------------------------------


def _reasons_context(request):
    scope = _scope(request)
    reasons = list(
        _visible(ExitReasonOption, request)
        .annotate(used=Count("letters"))
        .order_by("title")
    )
    for row in reasons:
        row.editable = row.company_id_id == scope
    return {
        "reason_groups": [
            (key, label, [r for r in reasons if r.exit_type == key])
            for key, label in ExitReasonOption._meta.get_field("exit_type").choices
        ],
        "scope_company": scope,
    }


def _policies_context(request):
    scope = _scope(request)
    policies = list(
        _visible(NoticePeriodPolicy, request)
        .annotate(people=Count("assignments"))
        .order_by("-is_default", "title")
    )
    for row in policies:
        row.editable = row.company_id_id == scope
    return {"policies": policies, "scope_company": scope}


SECTIONS = {
    "reasons": ("offboarding/settings/_xs_reasons.html", _reasons_context),
    "policies": ("offboarding/settings/_xs_policies.html", _policies_context),
}


@login_required
@permission_required(PERM)
def exit_settings_view(request):
    return render(
        request,
        "offboarding/settings/exit_settings.html",
        {
            "form": GeneralSettingsForm(
                instance=OffboardingGeneralSetting.for_editing(request)
            ),
            "scope_company": _scope(request),
            **_reasons_context(request),
            **_policies_context(request),
        },
    )


@login_required
@permission_required(PERM)
def exit_settings_section(request, name):
    """One section of the page on its own, so it can refresh after a change."""
    if name not in SECTIONS:
        raise Http404
    template, context = SECTIONS[name]
    return render(request, template, context(request))


# --------------------------------------------------------------------------
# resignation and termination rules
# --------------------------------------------------------------------------


@login_required
@permission_required(PERM)
@require_POST
def exit_settings_save(request):
    row = OffboardingGeneralSetting.for_editing(request)
    form = GeneralSettingsForm(request.POST, instance=row)
    if not form.is_valid():
        return _answer(request, error=_errors(form), close=False)
    saved = form.save(commit=False)
    saved.company_id_id = _scope(request)
    saved.save()
    return _answer(request, message=_("Exit settings saved."), close=False)


# --------------------------------------------------------------------------
# exit reasons
# --------------------------------------------------------------------------


@login_required
@permission_required(PERM)
def exit_reason_form(request, pk=None):
    """The add / rename window for a reason."""
    instance = get_object_or_404(_in_scope(ExitReasonOption, request), pk=pk) if pk else None
    exit_type = instance.exit_type if instance else request.GET.get("type", "resignation")
    labels = dict(ExitReasonOption._meta.get_field("exit_type").choices)
    if exit_type not in labels:
        raise Http404
    return render(
        request,
        "offboarding/settings/modals/reason_form.html",
        {"reason": instance, "exit_type": exit_type, "type_label": labels[exit_type]},
    )


@login_required
@permission_required(PERM)
@require_POST
def exit_reason_save(request, pk=None):
    instance = get_object_or_404(_in_scope(ExitReasonOption, request), pk=pk) if pk else None
    form = ReasonForm(request.POST, instance=instance)
    if not form.is_valid():
        return _answer(request, error=_errors(form), close=False)
    title, exit_type = form.cleaned_data["title"], form.cleaned_data["exit_type"]
    clash = _in_scope(ExitReasonOption, request).filter(
        title__iexact=title, exit_type=exit_type
    )
    if instance:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        return _answer(request, error=_("That reason already exists."), close=False)
    reason = form.save(commit=False)
    reason.company_id_id = _scope(request)
    reason.save()
    return _answer(request, message=_("Reason saved."))


@login_required
@permission_required(PERM)
@require_POST
def exit_reason_delete(request, pk):
    reason = get_object_or_404(_in_scope(ExitReasonOption, request), pk=pk)
    reason.delete()
    return _answer(request, message=_("Reason removed."))


# --------------------------------------------------------------------------
# notice period policies
# --------------------------------------------------------------------------


@login_required
@permission_required(PERM)
def notice_policy_form(request, pk=None):
    """The add / edit window for a policy."""
    instance = get_object_or_404(_in_scope(NoticePeriodPolicy, request), pk=pk) if pk else None
    return render(
        request,
        "offboarding/settings/modals/policy_form.html",
        {"policy": instance},
    )


@login_required
@permission_required(PERM)
@require_POST
def notice_policy_save(request, pk=None):
    instance = get_object_or_404(_in_scope(NoticePeriodPolicy, request), pk=pk) if pk else None
    form = PolicyForm(request.POST, instance=instance)
    if not form.is_valid():
        return _answer(request, error=_errors(form), close=False)
    clash = _in_scope(NoticePeriodPolicy, request).filter(
        title__iexact=form.cleaned_data["title"]
    )
    if instance:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        return _answer(request, error=_("A policy with that name already exists."), close=False)
    policy = form.save(commit=False)
    policy.company_id_id = _scope(request)
    policy.save()
    return _answer(request, message=_("Notice period policy saved."))


@login_required
@permission_required(PERM)
@require_POST
def notice_policy_default(request, pk):
    policy = get_object_or_404(_in_scope(NoticePeriodPolicy, request), pk=pk)
    policy.is_default = True
    policy.save()
    return _answer(
        request,
        message=_("'%(name)s' is now the default policy.") % {"name": policy.title},
    )


@login_required
@permission_required(PERM)
@require_POST
def notice_policy_delete(request, pk):
    policy = get_object_or_404(_in_scope(NoticePeriodPolicy, request), pk=pk)
    try:
        policy.delete()
    except ProtectedError:
        return _answer(
            request,
            error=_("This policy is assigned to employees. Move them to another policy first."),
            close=False,
        )
    return _answer(request, message=_("Policy deleted."))


def _people_context(request, policy):
    """Who is on `policy`, and what can be done with them."""
    assigned = list(
        EmployeeNoticePolicy.objects.entire()
        .filter(policy_id=policy)
        .select_related("employee_id")
        .order_by("employee_id__employee_first_name", "employee_id__employee_last_name")
    )
    assigned_ids = {row.employee_id_id for row in assigned}
    all_assigned = EmployeeNoticePolicy.objects.entire().count()
    active = Employee.objects.filter(is_active=True)
    return {
        "policy": policy,
        "assigned": assigned,
        "others": [
            p for p in _visible(NoticePeriodPolicy, request).order_by("title") if p.pk != policy.pk
        ],
        "candidates": active.exclude(pk__in=assigned_ids).order_by("employee_first_name"),
        "on_default_count": max(active.count() - all_assigned, 0) if policy.is_default else None,
        "editable": policy.company_id_id == _scope(request),
    }


@login_required
@permission_required(PERM)
def notice_policy_people(request, pk):
    """The window listing the people on a policy, with moving and removing them."""
    policy = get_object_or_404(_visible(NoticePeriodPolicy, request), pk=pk)
    return render(
        request,
        "offboarding/settings/modals/policy_people.html",
        _people_context(request, policy),
    )


@login_required
@permission_required(PERM)
@require_POST
def notice_policy_assign(request):
    """
    Put employees on a policy, or (policy left empty) back on the default one.
    `viewing` is the policy whose people window is open, so it can be redrawn.
    """
    form = AssignForm(request.POST)
    if not form.is_valid():
        return _answer(request, error=_("Choose at least one employee."), close=False)
    policy = form.cleaned_data["policy"]
    employees = form.cleaned_data["employees"]
    if policy is None:
        EmployeeNoticePolicy.objects.entire().filter(employee_id__in=employees).delete()
        message = _("%(n)s employee(s) are back on the default policy.") % {"n": len(employees)}
    else:
        for employee in employees:
            # .entire(): update_or_create locks the row, and PostgreSQL refuses FOR UPDATE with the
            # DISTINCT the company filter adds once a company is selected
            EmployeeNoticePolicy.objects.entire().update_or_create(
                employee_id=employee, defaults={"policy_id": policy}
            )
        message = _("%(n)s employee(s) assigned to '%(name)s'.") % {
            "n": len(employees),
            "name": policy.title,
        }
    viewing = _visible(NoticePeriodPolicy, request).filter(pk=request.POST.get("viewing") or 0).first()
    body = None
    if _htmx(request) and viewing is not None:
        body = render(
            request,
            "offboarding/settings/modals/policy_people.html",
            _people_context(request, viewing),
        )
    return _answer(request, message=message, body=body, close=False)
