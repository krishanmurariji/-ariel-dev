"""
recruitment/cbv/candidate_intake.py

CBV views for the Candidate Email Intake feature: connecting/configuring
an Outlook mailbox for candidate application intake, and viewing the log
of every email processed (created candidates and failed attempts).
"""

from typing import Any

from django.contrib import messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
)
from recruitment.filters import CandidateIntakeLogFilter, CandidateIntakeMailboxFilter
from recruitment.forms import CandidateIntakeMailboxForm
from recruitment.models import CandidateIntakeLog, CandidateIntakeMailbox


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidateintakemailbox"),
    name="dispatch",
)
class CandidateIntakeMailboxListView(HorillaListView):
    """
    List view for connected candidate intake mailboxes.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.view_id = "candidate_intake_mailbox"
        self.search_url = reverse("candidate-intake-mailbox-list")

    model = CandidateIntakeMailbox
    filter_class = CandidateIntakeMailboxFilter
    action_method = "action_template"
    show_toggle_form = False

    columns = [
        (_("Provider"), "get_provider_display"),
        (_("Mailbox"), "mailbox_account_display"),
        (_("Fallback Recruitment"), "fallback_recruitment_id"),
        (_("Last Polled"), "last_polled_at"),
        (_("Status"), "error_status"),
        (_("Active"), "is_active_toggle"),
    ]


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidateintakemailbox"),
    name="dispatch",
)
class CandidateIntakeMailboxNav(HorillaNavView):
    """
    Nav bar for the candidate intake mailbox settings page.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("candidate-intake-mailbox-list")
        if self.request.user.has_perm("recruitment.add_candidateintakemailbox"):
            self.create_attrs = f"""
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                hx-get="{reverse('candidate-intake-mailbox-create')}"
                """

    nav_title = _("Candidate Intake Mailboxes")
    search_swap_target = "#listContainer"


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.add_candidateintakemailbox"),
    name="dispatch",
)
class CandidateIntakeMailboxFormView(HorillaFormView):
    """
    Create/update form for a candidate intake mailbox.
    """

    model = CandidateIntakeMailbox
    form_class = CandidateIntakeMailboxForm
    new_display_title = _("Connect Candidate Intake Mailbox")
    template_name = "cbv/candidate_intake_mailbox_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Update Candidate Intake Mailbox")
        return context

    def form_valid(self, form: CandidateIntakeMailboxForm) -> HttpResponse:
        if form.is_valid():
            if form.instance.pk:
                message = _("Mailbox updated successfully.")
            else:
                message = _("Mailbox connected successfully.")
            form.save()
            messages.success(self.request, _(message))
            return self.HttpResponse()
        return super().form_valid(form)


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidateintakelog"), name="dispatch"
)
class CandidateIntakeLogListView(HorillaListView):
    """
    Read-only list view of every email the intake mailboxes have processed,
    successful or not, with the reason for any failure.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.view_id = "candidate_intake_log"
        self.search_url = reverse("candidate-intake-log-list")
        detail_url = reverse(
            "candidate-intake-log-detail", kwargs={"pk": 0}
        ).replace("/0/", "/{id}/")
        self.row_attrs = f"""
            hx-get="{detail_url}"
            hx-target="#genericModalBody"
            data-toggle="oh-modal-toggle"
            data-target="#genericModal"
            style="cursor:pointer"
            """

    model = CandidateIntakeLog
    filter_class = CandidateIntakeLogFilter
    show_toggle_form = False

    columns = [
        (_("Received"), "received_at"),
        (_("From"), "from_email"),
        (_("Subject"), "subject"),
        (_("Status"), "status_badge"),
        (_("Resume"), "resume_link"),
        (_("Candidate"), "candidate"),
        (_("Matched Recruitment"), "matched_recruitment"),
        (_("Reason"), "error_message"),
    ]


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidateintakelog"), name="dispatch"
)
class CandidateIntakeLogNav(HorillaNavView):
    """
    Nav bar for the intake log page: search/filter plus a live poll-status
    indicator (with a "Run Poll Now" trigger, disabled while a poll is
    already running) embedded next to the title, self-refreshing every
    5 seconds.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("candidate-intake-log-list")
        status_url = reverse("candidate-intake-poll-status")
        self.nav_title = mark_safe(
            f'{_("Email Applications")} '
            f'<span id="pollStatusInline" class="text-muted" '
            f'style="font-size:0.8rem; font-weight:normal; margin-left:0.5rem;" '
            f'hx-get="{status_url}" hx-trigger="load, every 5s" hx-swap="innerHTML">'
            f"</span>"
        )
        if self.request.user.has_perm("recruitment.change_candidateintakemailbox"):
            self.actions = [
                {
                    "action": _("Retry Selected"),
                    "icon": "reload-outline",
                    "attrs": 'onclick="retrySelectedIntakeLogs(); return false;"',
                }
            ]

    filter_instance = CandidateIntakeLogFilter()
    search_swap_target = "#listContainer"
