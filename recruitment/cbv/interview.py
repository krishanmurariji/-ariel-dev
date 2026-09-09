"""
this page handles cbv of interview page
"""

from typing import Any

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import resolve, reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _

from horilla_views.cbv_methods import login_required
from horilla_views.generic.cbv.views import (
    HorillaDetailedView,
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from notifications.signals import notify
from recruitment.decorators import manager_can_enter, recruitment_manager_can_enter
from recruitment.filters import InterviewFilter
from recruitment.forms import ScheduleInterviewForm
from recruitment.models import Candidate, InterviewSchedule


@method_decorator(login_required, name="dispatch")
@method_decorator(
    recruitment_manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class InterviewViewPage(TemplateView):
    """
    for interview page
    """

    template_name = "cbv/interview/interview_home_view.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(
    recruitment_manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class InterviewNavView(HorillaNavView):
    """
    nav bar of the page
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("interview-list-view")

        if self.request.user.has_perm("recruitment.add_interviewschedule"):
            self.create_attrs = f"""
                hx-get="{reverse_lazy("create-interview-schedule")}"
                hx-target="#genericModalBody"
                data-target="#genericModal"
                data-toggle="oh-modal-toggle"
            """
        else:
            self.create_attrs = None

    nav_title = _("Interviews")
    filter_instance = InterviewFilter()
    filter_body_template = "cbv/interview/interview_filter.html"
    filter_form_context_name = "form"
    search_swap_target = "#listContainer"


@method_decorator(login_required, name="dispatch")
@method_decorator(
    recruitment_manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class InterviewLIstView(HorillaListView):
    """
    list view of the page
    """

    bulk_update_fields = ["employee_id", "interview_date", "interview_time"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("interview-list-view")
        self.view_id = "interviewdelete"

    def get_queryset(self):
        """
        Override queryset based on user permissions.

        Returns:
            queryset: Filtered queryset based on user permissions and employee ID.
        """
        queryset = super().get_queryset()
        if self.request.user.has_perm("recruitment.view_interviewschedule"):
            queryset = queryset.all().order_by("-interview_date")
        else:
            queryset = queryset.filter(
                employee_id=self.request.user.employee_get.id
            ).order_by("-interview_date")

        return queryset

    filter_class = InterviewFilter
    model = InterviewSchedule
    records_per_page = 10
    template_name = "cbv/interview/inherit_script.html"

    columns = [
        (_("Candidate"), "candidate_custom_col"),
        (_("Job Opening"), "candidate_id__recruitment_id__title"),
        (_("Interviewer"), "interviewer_custom_col"),
        (_("Interview Date"), "interview_date"),
        (_("Interview Time"), "interview_time"),
        (_("Description"), "get_description"),
        (_("Status"), "status_custom_col"),
    ]
    header_attrs = {
        "candidate_custom_col": """
                                style="width:200px !important;"
                                """
    }

    sortby_mapping = [
        (_("Interview Date"), "interview_date"),
        (_("Interview Time"), "interview_time"),
    ]
    action_method = "custom_action_col"

    row_attrs = """
                {custom_color}
                class="oh-permission-table--collapsed"
                hx-get='{detail_view}?instance_ids={ordered_ids}'
                hx-target="#genericModalBody"
                data-target="#genericModal"
                data-toggle="oh-modal-toggle"
                """


@method_decorator(login_required, name="dispatch")
@method_decorator(
    recruitment_manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class InterviewDetailView(HorillaDetailedView):
    """
    detailed view
    """

    model = InterviewSchedule
    title = _("Details")
    header = {
        "title": "candidate_id__get_full_name",
        "subtitle": "detail_subtitle",
        "avatar": "candidate_id__get_avatar",
    }
    body = [
        (_("Candidate"), "candidate_id"),
        (_("Interviewer"), "interviewer_detail"),
        (_("Interview Date"), "interview_date"),
        (_("Interview Time"), "interview_time"),
        (_("Description"), "get_description"),
        (_("Status"), "status_custom_col"),
    ]
    action_method = "detail_view_actions"


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.add_interviewschedule"), name="dispatch"
)
class InterviewForm(HorillaFormView):
    """
    form view
    """

    form_class = ScheduleInterviewForm
    model = InterviewSchedule
    new_display_title = _("Schedule Interview")

    def get_context_data(self, **kwargs):
        """
        Override to add custom context data.
        """
        context = super().get_context_data(**kwargs)
        resolved = resolve(self.request.path_info)
        cand_id = resolved.kwargs.get("cand_id")
        if cand_id:
            self.form.fields["candidate_id"].queryset = Candidate.objects.filter(
                id=cand_id
            )
            self.form.fields["candidate_id"].initial = cand_id
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Schedule Interview")
        context["form"] = self.form
        context["view_id"] = "InterviewCreate"
        return context

    def form_invalid(self, form: Any) -> HttpResponse:
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Schedule Interview")
        if not form.is_valid():
            context = self.get_context_data(form=form)
            context["errors"] = form.errors.as_data()
            return render(self.request, self.template_name, context)
        return super().form_invalid(form)

    def form_valid(self, form: ScheduleInterviewForm) -> HttpResponse:
        """
        Handle form submission when the form is valid.

        Args:
            form (ScheduleInterviewForm): The validated form object.

        Returns:
            HttpResponse: Redirect response or HTTP response.
        """
        if form.is_valid():
            emp_ids = self.form.cleaned_data["employee_id"]
            cand_id = self.form.cleaned_data["candidate_id"]
            interview_date = self.form.cleaned_data["interview_date"]
            interview_time = self.form.cleaned_data["interview_time"]

            is_update = bool(form.instance.pk)
            # Only re-notify when a field that actually affects the
            # calendar invite changed. Editing just `completed` (or
            # `is_active`, not form-editable at all) must not re-notify
            # everyone with a calendar update that misleadingly reads as a
            # reschedule - see the bug this guards against: ticking "Mark
            # as Complete" alone used to fire the exact same "Interview
            # Re-scheduled" mail as an actual date/time change, since both
            # went through this same save path with no notion of what was
            # actually edited.
            #
            # form.changed_data can't be trusted here: on edit,
            # interview_time is swapped for a permissive CharField (see
            # __init__ above) so browser round-trip formatting doesn't
            # fail validation, and Django computes changed_data from that
            # field's own raw cleaned value *before* this form's clean()
            # re-parses it back into a real time - so it always reports
            # "changed" even when the time is identical. Comparing the
            # actual saved values against form.cleaned_data (already
            # normalized by clean()) instead sidesteps that entirely.
            schedule_relevant_changed = True
            if is_update:
                old = (
                    InterviewSchedule.objects.filter(pk=form.instance.pk)
                    .values(
                        "candidate_id",
                        "interview_date",
                        "interview_time",
                        "interview_mode",
                        "description",
                    )
                    .first()
                )
                old_employee_ids = set(
                    InterviewSchedule.objects.get(pk=form.instance.pk)
                    .employee_id.values_list("pk", flat=True)
                )
                new_employee_ids = {employee.pk for employee in emp_ids}
                schedule_relevant_changed = bool(old) and (
                    old["candidate_id"] != cand_id.pk
                    or old["interview_date"] != interview_date
                    or old["interview_time"] != interview_time
                    or old["interview_mode"] != form.cleaned_data["interview_mode"]
                    or old["description"] != form.cleaned_data.get("description", "")
                    or old_employee_ids != new_employee_ids
                )
            should_notify = not is_update or schedule_relevant_changed

            if should_notify:
                users = [employee.employee_user_id for employee in emp_ids]
                if is_update:
                    verb = f"Your interview with {cand_id.name} has been rescheduled to {interview_date} at {interview_time}."
                    verb_ar = f"تمت إعادة جدولة مقابلتك مع {cand_id.name} إلى {interview_date} في توقيت {interview_time}."
                    verb_de = f"Ihr Interview mit {cand_id.name} wurde auf den {interview_date} um {interview_time} verschoben."
                    verb_es = f"Tu entrevista con {cand_id.name} ha sido reprogramada para el {interview_date} a las {interview_time}."
                    verb_fr = f"Votre entretien avec {cand_id.name} a été reprogrammé au {interview_date} à {interview_time}."
                else:
                    verb = f"You are scheduled as an interviewer for an interview with {cand_id.name} on {interview_date} at {interview_time}."
                    verb_ar = f"أنت مجدول كمقابلة مع {cand_id.name} يوم {interview_date} في توقيت {interview_time}."
                    verb_de = f"Sie sind als Interviewer für ein Interview mit {cand_id.name} am {interview_date} um {interview_time} eingeplant."
                    verb_es = f"Estás programado como entrevistador para una entrevista con {cand_id.name} el {interview_date} a las {interview_time}."
                    verb_fr = f"Vous êtes programmé en tant qu'intervieweur pour un entretien avec {cand_id.name} le {interview_date} à {interview_time}."
                notify.send(
                    self.request.user.employee_get,
                    recipient=users,
                    verb=verb,
                    verb_ar=verb_ar,
                    verb_de=verb_de,
                    verb_es=verb_es,
                    verb_fr=verb_fr,
                    icon="people-circle",
                    redirect=reverse("interview-view"),
                )
            if is_update:
                messages.success(self.request, _("Interview Updated Successfully"))
            else:
                messages.success(self.request, _("Interview Scheduled successfully."))
            form.save()

            if not should_notify:
                # Nothing schedule-relevant changed (e.g. only "Mark as
                # Complete" was toggled) - skip touching the calendar
                # event entirely, so no attendee gets a mail at all.
                return HttpResponse(
                    "<script>"
                    "$('#genericModal').removeClass('oh-modal--show');"
                    "$('#applyFilter').click();"
                    "$('#reloadMessagesButton').click();"
                    "</script>"
                )

            # Best-effort: a candidate/interviewer without the meeting
            # provider connected yet (needs_auth) or a provider-side
            # error must not undo the interview that was just scheduled
            # or updated - they can still generate/refresh it later via
            # the existing "Send Meeting Link" action. This runs whenever
            # a schedule-relevant field changed (including on creation),
            # so that adding/removing interviewers or changing the
            # date/time/mode/description pushes an updated attendee list
            # to the provider, which is what triggers the add/cancellation
            # emails for changed attendees.
            from recruitment.views.views import generate_interview_meeting

            meeting_result = generate_interview_meeting(form.instance, self.request)
            is_online = form.instance.interview_mode != "offline"
            if meeting_result.get("success"):
                if meeting_result.get("created"):
                    messages.success(
                        self.request,
                        _("Meeting link generated.")
                        if is_online
                        else _("Interview added to calendar and candidate notified."),
                    )
                else:
                    messages.success(
                        self.request,
                        _("Meeting updated and invitees notified.")
                        if is_online
                        else _("Calendar event updated and invitees notified."),
                    )
            elif meeting_result.get("needs_auth"):
                messages.warning(
                    self.request,
                    meeting_result.get("message")
                    or (
                        _(
                            "Interview saved, but the meeting link could "
                            "not be generated: the meeting provider isn't "
                            "connected yet. Use 'Send Meeting Link' from the "
                            "interview once it's connected."
                        )
                        if is_online
                        else _(
                            "Interview saved, but it could not be added to "
                            "the calendar: the meeting provider isn't "
                            "connected yet."
                        )
                    ),
                )
            elif meeting_result.get("error"):
                messages.warning(
                    self.request,
                    (
                        _(
                            "Interview saved, but the meeting link could "
                            "not be generated: %(error)s"
                        )
                        if is_online
                        else _(
                            "Interview saved, but it could not be added to "
                            "the calendar: %(error)s"
                        )
                    )
                    % {"error": meeting_result["error"]},
                )

            return HttpResponse(
                "<script>"
                "$('#genericModal').removeClass('oh-modal--show');"
                "$('#applyFilter').click();"
                "$('#reloadMessagesButton').click();"
                "</script>"
            )
        return super().form_valid(form)
