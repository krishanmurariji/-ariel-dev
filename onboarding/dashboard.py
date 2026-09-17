"""
Modern onboarding dashboard views — KPI summary + ApexCharts.

Accessible at /onboarding/dashboard/modern/ alongside the existing dashboard.

All data here is driven by the "Configure Onboarding" stepper system
(CandidateChecklistTask / OnboardingCandidateDetail / CandidateDocumentRequest
/ CandidateDocument), not the older Kanban pipeline (CandidateStage /
CandidateTask / OnboardingStage / OnboardingTask), which is still fully
functional but no longer surfaced in the sidebar.
"""

import json
from datetime import date, timedelta
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render

from horilla.methods import handle_no_permission

# See recruitment/dashboard.py's matching constant/decorator for the full
# rationale - same problem (every widget recomputed from scratch on every
# load, multiplying badly under concurrent viewers), same fix.
DASHBOARD_CACHE_TTL = 45


def _has_onboarding_permission(request):
    """Return True if the user may access the onboarding dashboard."""
    user = request.user
    if (
        user.is_superuser
        or user.has_perm("onboarding.view_onboardingstage")
        or user.has_perm("recruitment.view_candidate")
    ):
        return True
    try:
        employee = user.employee_get
        return (
            employee.onboardingstage_set.all().exists()
            or employee.onboarding_task.all().exists()
            or employee.checklist_tasks.exists()
        )
    except Exception:
        return False


def cache_dashboard_json(prefix, ttl=DASHBOARD_CACHE_TTL, per_user=False):
    """
    Same approach as recruitment/dashboard.py's cache_dashboard_json:
    permission is re-checked on every request before the cache is ever
    consulted, so a viewer without access can never be served a cached
    response meant for someone who has it.

    Set per_user=True for an endpoint whose data itself (not just the
    permission to view it) depends on which user is asking - e.g.
    onboarding_task_managers, which returns the requesting user's own
    assigned tasks. Getting this wrong the other way (leaving it False for
    a per-user endpoint) would serve one user's personal data to another,
    so it isn't just a cache-hit-rate knob.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _has_onboarding_permission(request):
                return JsonResponse({"no_permission": True})

            company = request.session.get("selected_company", "all")
            user_part = f":u{request.user.pk}" if per_user else ""
            cache_key = (
                f"onb_dash:{prefix}:{company}{user_part}:"
                f"{request.GET.urlencode()}"
            )
            cached = cache.get(cache_key)
            if cached is not None:
                return JsonResponse(cached)

            response = view_func(request, *args, **kwargs)
            if (
                isinstance(response, JsonResponse)
                and response.status_code == 200
            ):
                try:
                    cache.set(cache_key, json.loads(response.content), ttl)
                except (ValueError, TypeError):
                    pass
            return response

        return wrapper

    return decorator


def _parse_period(request):
    """Parse from_date and to_date from GET params. Defaults to current month."""
    today = date.today()
    from_str = request.GET.get("from_date")
    to_str = request.GET.get("to_date")
    try:
        from_date = date.fromisoformat(from_str) if from_str else today.replace(day=1)
    except (ValueError, TypeError):
        from_date = today.replace(day=1)
    try:
        to_date = date.fromisoformat(to_str) if to_str else today
    except (ValueError, TypeError):
        to_date = today
    return from_date, to_date


def _onboarding_candidates_in_period(request):
    """Return Candidate queryset (start_onboard=True) filtered to the requested period."""
    from recruitment.models import Candidate

    from_date, to_date = _parse_period(request)
    return Candidate.objects.filter(
        start_onboard=True,
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    )


@login_required
def onboarding_dashboard_view(request):
    """Render the modern onboarding dashboard page."""
    if not _has_onboarding_permission(request):
        return handle_no_permission(request)
    return render(request, "onboarding/dashboard.html")


@login_required
@cache_dashboard_json("kpi")
def onboarding_kpi_data(request):
    """Return onboarding KPI summary data as JSON."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import (
        CandidateChecklistTask,
        OnboardingCandidateDetail,
    )
    from recruitment.models import Candidate, CandidateDocument, CandidateDocumentRequest, Recruitment

    period_candidates = _onboarding_candidates_in_period(request)
    total_candidates = period_candidates.count()
    active_recruitments = Recruitment.objects.filter(
        closed=False, is_active=True
    ).count()

    # Task stats — restrict to tasks belonging to candidates in the selected period
    period_tasks = CandidateChecklistTask.objects.filter(
        candidate_id__in=period_candidates
    )
    total_tasks = period_tasks.count()
    completed_tasks = period_tasks.filter(status="done").count()
    stuck_tasks = period_tasks.filter(status="stuck").count()
    task_completion = (
        round((completed_tasks / total_tasks * 100), 1) if total_tasks > 0 else 0
    )
    tasks_pending = period_tasks.exclude(status="done").count()

    # Candidates who fully completed onboarding — within the period
    completed_onboarding = OnboardingCandidateDetail.objects.filter(
        candidate_id__in=period_candidates,
        onboarding_completed_at__isnull=False,
    ).count()

    # Candidates in progress (not yet marked complete)
    in_progress = total_candidates - completed_onboarding

    today = date.today()
    upcoming_joiners = (
        Candidate.objects.filter(is_active=True, joining_date__gte=today)
        .filter(Q(hired=True) | Q(start_onboard=True))
        .count()
    )

    joined_this_month = Candidate.objects.filter(
        is_active=True,
        joining_date__year=today.year,
        joining_date__month=today.month,
    ).count()

    doc_requests = CandidateDocumentRequest.objects.filter(
        candidate_id__in=period_candidates
    )
    submitted_request_ids = set(
        CandidateDocument.objects.filter(document_request_id__in=doc_requests)
        .exclude(document="")
        .exclude(document__isnull=True)
        .values_list("document_request_id", flat=True)
    )
    documents_pending = doc_requests.exclude(pk__in=submitted_request_ids).count()
    documents_under_verification = CandidateDocument.objects.filter(
        candidate_id__in=period_candidates, status="under_verification"
    ).count()

    return JsonResponse(
        {
            "total_candidates": total_candidates,
            "active_recruitments": active_recruitments,
            "completed_onboarding": completed_onboarding,
            "in_progress": in_progress,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "stuck_tasks": stuck_tasks,
            "task_completion": task_completion,
            "upcoming_joiners": upcoming_joiners,
            "documents_pending": documents_pending,
            "documents_under_verification": documents_under_verification,
            "tasks_pending": tasks_pending,
            "joined_this_month": joined_this_month,
        }
    )


@login_required
@cache_dashboard_json("stage_distribution")
def onboarding_stage_distribution(request):
    """Candidates by furthest-reached stepper step ("Stepper Progress")."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import OnboardingCandidateDetail
    from onboarding.stepper import STEPS

    period_candidates = _onboarding_candidates_in_period(request)
    counts = dict(
        OnboardingCandidateDetail.objects.filter(candidate_id__in=period_candidates)
        .values("furthest_step_key")
        .annotate(count=Count("id"))
        .values_list("furthest_step_key", "count")
    )

    stages = [
        {
            "id": key,
            "stage": str(label),
            "count": counts.get(key, 0),
            "is_final": key == "finalize",
            "recruitment": "",
        }
        for key, label in STEPS
    ]

    return JsonResponse({"stages": stages})


@login_required
@cache_dashboard_json("task_status")
def onboarding_task_status(request):
    """Task status breakdown."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import CandidateChecklistTask

    period_candidates = _onboarding_candidates_in_period(request)
    period_tasks = CandidateChecklistTask.objects.filter(
        candidate_id__in=period_candidates
    )

    statuses = []
    for status, label in CandidateChecklistTask.STATUS_CHOICES:
        count = period_tasks.filter(status=status).count()
        statuses.append({"status": status, "label": str(label), "count": count})

    return JsonResponse({"statuses": statuses})


@login_required
@cache_dashboard_json("by_recruitment")
def onboarding_by_recruitment(request):
    """Candidates onboarding per recruitment."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    recruitments = []

    try:
        data = (
            _onboarding_candidates_in_period(request)
            .values("recruitment_id__title")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        for item in data:
            title = item["recruitment_id__title"]
            if title:
                recruitments.append({"recruitment": title, "count": item["count"]})
    except Exception:
        pass

    return JsonResponse({"recruitments": recruitments})


@login_required
@cache_dashboard_json("by_job_position")
def onboarding_by_job_position(request):
    """Candidates onboarding per job position."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    positions = []

    try:
        data = (
            _onboarding_candidates_in_period(request)
            .values("job_position_id", "job_position_id__job_position")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        for item in data:
            pos = item["job_position_id__job_position"]
            pos_id = item["job_position_id"]
            if pos:
                positions.append(
                    {"position": pos, "position_id": pos_id, "count": item["count"]}
                )
    except Exception:
        pass

    return JsonResponse({"positions": positions})


@login_required
@cache_dashboard_json("candidates_list")
def onboarding_candidates_list(request):
    """Current onboarding candidates with progress."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import CandidateChecklistTask, OnboardingCandidateDetail
    from onboarding.stepper import STEP_LABELS

    candidates = []

    try:
        qs = (
            _onboarding_candidates_in_period(request)
            .select_related("recruitment_id", "job_position_id")
            .order_by("-id")[:15]
        )

        for c in qs:
            detail = OnboardingCandidateDetail.objects.filter(candidate_id=c).first()
            stage_name = (
                str(STEP_LABELS.get(detail.furthest_step_key, "—")) if detail else "—"
            )

            # Task progress
            total = CandidateChecklistTask.objects.filter(candidate_id=c).count()
            done = CandidateChecklistTask.objects.filter(
                candidate_id=c, status="done"
            ).count()
            progress = round((done / total * 100)) if total > 0 else 0

            candidates.append(
                {
                    "id": c.id,
                    "name": c.name or "—",
                    "profile": c.profile.url if c.profile else None,
                    "recruitment": c.recruitment_id.title if c.recruitment_id else "—",
                    "position": (
                        c.job_position_id.job_position if c.job_position_id else "—"
                    ),
                    "stage": stage_name,
                    "progress": progress,
                    "tasks_done": done,
                    "tasks_total": total,
                }
            )
    except Exception:
        pass

    return JsonResponse({"candidates": candidates})


@login_required
@cache_dashboard_json("task_managers", per_user=True)
def onboarding_task_managers(request):
    """The logged-in user's own assigned checklist tasks, grouped by task title."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import CandidateChecklistTask

    tasks = []

    try:
        user_emp = getattr(request.user, "employee_get", None)
        if user_emp:
            period_candidates = _onboarding_candidates_in_period(request)
            groups = (
                CandidateChecklistTask.objects.filter(
                    assignee_id=user_emp, candidate_id__in=period_candidates
                )
                .values("title")
                .annotate(
                    total=Count("id"),
                    done=Count("id", filter=Q(status="done")),
                    stuck=Count("id", filter=Q(status="stuck")),
                )
                .order_by("-total")[:10]
            )

            for group in groups:
                total = group["total"]
                tasks.append(
                    {
                        "title": group["title"],
                        "stage": "",
                        "total": total,
                        "done": group["done"],
                        "stuck": group["stuck"],
                        "progress": (
                            round((group["done"] / total * 100)) if total > 0 else 0
                        ),
                    }
                )
    except Exception:
        pass

    return JsonResponse({"tasks": tasks})


@login_required
@cache_dashboard_json("completion_trend")
def onboarding_completion_trend(request):
    """Monthly onboarding completions within the selected period."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import OnboardingCandidateDetail

    period_candidates = _onboarding_candidates_in_period(request)
    from_date, to_date = _parse_period(request)
    months = []
    try:
        cursor = from_date.replace(day=1)
        end_marker = to_date.replace(day=1)
        while cursor <= end_marker:
            if cursor.month == 12:
                next_month = cursor.replace(year=cursor.year + 1, month=1)
            else:
                next_month = cursor.replace(month=cursor.month + 1)
            month_end = next_month - timedelta(days=1)
            count = OnboardingCandidateDetail.objects.filter(
                candidate_id__in=period_candidates,
                onboarding_completed_at__date__gte=cursor,
                onboarding_completed_at__date__lte=month_end,
            ).count()
            months.append({"month": cursor.strftime("%b %Y"), "count": count})
            cursor = next_month
    except Exception:
        pass
    return JsonResponse({"months": months})


@login_required
@cache_dashboard_json("portal_status")
def onboarding_portal_status(request):
    """Portal access status for onboarding candidates within the selected period."""
    if not _has_onboarding_permission(request):
        return JsonResponse({"no_permission": True})
    from onboarding.models import OnboardingPortal

    portals = []
    try:
        period_candidates = _onboarding_candidates_in_period(request)
        qs = (
            OnboardingPortal.objects.filter(candidate_id__in=period_candidates)
            .select_related("candidate_id")
            .order_by("-count")[:15]
        )
        for p in qs:
            cand = p.candidate_id
            portals.append(
                {
                    "id": cand.id if cand else None,
                    "name": cand.name if cand else "—",
                    "used": p.used,
                    "visits": p.count,
                }
            )
    except Exception:
        pass
    return JsonResponse({"portals": portals})
