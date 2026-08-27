"""
Onboarding candidate view.
"""

from typing import Any

from django.db.models import Q
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _

from base.models import HorillaMailTemplate
from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaCardView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from recruitment.filters import CandidateFilter
from recruitment.models import Candidate, CandidateDocument


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidate"), name="dispatch"
)
class OnboardingCandidatesView(TemplateView):
    """
    onboarding candidates view
    """

    template_name = "cbv/onboarding_candidates/onboarding_candidates.html"

    def get_context_data(self, **kwargs: Any):
        context = super().get_context_data(**kwargs)
        hired_candidates = Candidate.objects.filter(
            is_active=True,
            recruitment_id__closed=False,
        ).filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
        mail_templates = HorillaMailTemplate.objects.all()
        context["mail_templates"] = mail_templates
        context["hired_candidates"] = hired_candidates
        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidate"), name="dispatch"
)
class OnboardingCandidatesList(HorillaListView):
    """
    List view
    """

    bulk_update_fields = [
        "joining_date",
        "probation_end",
        "job_position_id",
        "recruitment_id",
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("onboarding-candidates-list")

    def get_queryset(self):
        if not getattr(self, "queryset"):
            queryset = super().get_queryset()
            self.queryset = (
                queryset.filter(
                    is_active=True,
                    recruitment_id__closed=False,
                )
                .filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
                .distinct()
            ).order_by("-id")

        return self.queryset

    model = Candidate
    filter_class = CandidateFilter

    columns = [
        (_("Candidate"), "name", "get_avatar"),
        (_("Email"), "last_email"),
        (_("Date of joining"), "date_of_joining"),
        (_("Probation ends"), "probation_date"),
        (_("Job position"), "job_position_id"),
        (_("Recruitment"), "recruitment_id"),
        (_("Offer letter"), "offer_letter"),
    ]
    header_attrs = {
        "action": "style='width: 350px;'",
    }

    action_method = "actions"

    row_status_indications = [
        (
            "joining--dot",
            _("Joining Set"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=joining_set]').val('true');
                $('[name=portal_sent]').val('unknown').change();
                $('#applyFilter').click();

            "
            """,
        ),
        (
            "not-joining--dot",
            _("Joining Not-Set"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=joining_set]').val('false');
                $('[name=portal_sent]').val('unknown').change();
                $('#applyFilter').click();

            "
            """,
        ),
        (
            "not-portal--dot",
            _("Portal Not-Sent"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=portal_sent]').val('false');
                $('[name=joining_set]').val('unknown').change();
                $('#applyFilter').click();
            "
            """,
        ),
        (
            "portal--dot",
            _("Portal Send"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=portal_sent]').val('true');
                $('[name=joining_set]').val('unknown').change();
                $('#applyFilter').click();

            "
            """,
        ),
    ]

    sortby_mapping = [
        (_("Candidate"), "name"),
        (_("Email"), "last_email"),
        (_("Date of joining"), "date_of_joining"),
        (_("Job position"), "job_position_id__job_position"),
        (_("Recruitment"), "recruitment_id__title"),
        (_("Probation ends"), "probation_date"),
    ]

    row_attrs = """
                onclick="
                    try {{ sessionStorage.setItem('candidateProfileFrom', 'onboarding'); }} catch (e) {{}}
                "
                hx-get="{get_profile_url}?from=onboarding&instance_ids={ordered_ids}"
                hx-target="#listContainer"
                hx-swap="innerHTML"
                hx-push-url="{get_individual_url}"
                class="cursor-pointer"
                """


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidate"), name="dispatch"
)
class OnboardingCandidateCard(HorillaCardView):
    """
    Long vertical candidate card view - profile photo + Configure Onboarding
    arrow button on the left, details on the right.
    """

    model = Candidate
    filter_class = CandidateFilter
    template_name = "cbv/onboarding_candidates/candidate_card.html"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("onboarding-candidate-card-view")

    def get_queryset(self):
        if not getattr(self, "queryset"):
            queryset = super().get_queryset()
            self.queryset = (
                queryset.filter(
                    is_active=True,
                    recruitment_id__closed=False,
                )
                .filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
                .distinct()
            ).order_by("-id")
        return self.queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for candidate in context["queryset"]:
            tasks = list(candidate.checklist_tasks.all())
            total = len(tasks)
            done = len(
                [t for t in tasks if t.status == "done"]
            )
            candidate.checklist_total = total
            candidate.checklist_done = done
            candidate.checklist_pct = int(done / total * 100) if total else 0

            detail = getattr(candidate, "onboarding_detail", None)
            candidate.onboarding_completed_at = (
                detail.onboarding_completed_at if detail else None
            )
            basic_fields = [
                candidate.job_position_id_id,
                candidate.joining_date,
                candidate.probation_end,
            ]
            if detail:
                basic_fields += [
                    detail.department_id_id,
                    detail.job_role_id_id,
                    detail.reporting_manager_id_id,
                    detail.buddy_id_id,
                    detail.shift_id_id,
                    detail.work_type_id_id,
                    detail.employee_type_id_id,
                ]
            basic_filled = len([f for f in basic_fields if f])
            candidate.basic_details_pct = (
                int(basic_filled / len(basic_fields) * 100) if basic_fields else 0
            )

            doc_requests = list(candidate.candidatedocumentrequest_set.all())
            doc_total = len(doc_requests)
            doc_verified = (
                CandidateDocument.objects.filter(
                    document_request_id__in=doc_requests, status="approved"
                ).count()
                if doc_requests
                else 0
            )
            candidate.documents_total = doc_total
            candidate.documents_verified = doc_verified
            candidate.documents_pct = (
                int(doc_verified / doc_total * 100) if doc_total else 0
            )
        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_candidate"), name="dispatch"
)
class OnboardingCandidatesNav(HorillaNavView):
    """
    Nav bar
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("onboarding-candidates-list")
        self.create_attrs = f"""
                                href="{reverse_lazy('candidate-create')}?onboarding=True"
                                """
        self.filter_instance = CandidateFilter()
        self.view_types = [
            {
                "type": "list",
                "icon": "list-outline",
                "url": reverse("onboarding-candidates-list"),
                "attrs": f"""title='{_("List")}'""",
            },
            {
                "type": "card",
                "icon": "grid-outline",
                "url": reverse("onboarding-candidate-card-view"),
                "attrs": f"""title='{_("Card")}'""",
            },
        ]

    nav_title = _("Hired Candidates")
    filter_body_template = "cbv/onboarding_candidates/filter.html"
    filter_form_context_name = "form"
    search_swap_target = "#listContainer"

    group_by_fields = [
        ("recruitment_id", _("Recruitment")),
        ("job_position_id", _("Job position")),
        ("country", _("Country")),
        ("stage_id", _("Stage")),
        ("joining_date", _("Joining Date")),
        ("probation_end", _("Probation End")),
        ("offer_letter_status", _("Offer Letter Status")),
        ("rejected_candidate__reject_reason_id", _("Rejected Reason")),
        ("skillzonecandidate_set__skill_zone_id", _("Skill Zone")),
    ]

    actions = [
        {
            "action": _("Send Portal"),
            "attrs": """

                    data-target="#addAttachments"
                    data-toggle="oh-modal-toggle"
                    id="send-port"
                    """,
        }
    ]
