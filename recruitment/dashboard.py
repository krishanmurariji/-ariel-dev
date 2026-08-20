"""
Modern recruitment dashboard views — KPI summary + ApexCharts.

Accessible at /recruitment/dashboard/modern/ alongside the existing dashboard.
"""

from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.methods import handle_no_permission
from recruitment.models import Recruitment, Stage


def _has_recruitment_permission(request):
    """Return True if the user may access the recruitment dashboard."""
    user = request.user
    if user.is_superuser or user.has_perm("recruitment.view_recruitment"):
        return True
    employee = Employee.objects.filter(employee_user_id=user).first()
    return employee is not None and (
        Stage.objects.filter(stage_managers=employee).exists()
        or Recruitment.objects.filter(recruitment_managers=employee).exists()
    )


def _platform_conversion_widget_enabled(request):
    """Whether the Source Conversion Rate widget is enabled for the
    currently selected company (defaults to enabled, mirrors
    base.context_processors.check_platform_conversion_widget)."""
    from recruitment.models import RecruitmentGeneralSetting

    selected_company = request.session.get("selected_company")
    if selected_company and selected_company != "all":
        settings = RecruitmentGeneralSetting.objects.filter(
            company_id_id=selected_company
        ).first()
    else:
        settings = RecruitmentGeneralSetting.objects.filter(
            company_id__isnull=True
        ).first()
    return settings.show_platform_conversion if settings else True


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


def _candidates_in_period(request):
    """Return Candidate queryset filtered to the requested period (by created_at)."""
    from recruitment.models import Candidate

    from_date, to_date = _parse_period(request)
    return Candidate.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    )


@login_required
def recruitment_dashboard_view(request):
    """Render the modern recruitment dashboard page."""
    if not _has_recruitment_permission(request):
        return handle_no_permission(request)
    return render(request, "recruitment/dashboard.html")


@login_required
def recruitment_kpi_data(request):
    """Return recruitment KPI summary data as JSON."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Candidate, Recruitment, Stage

    recruitments = Recruitment.objects.filter(closed=False, is_event_based=False)
    ongoing = recruitments.count()

    total_vacancy = 0
    for rec in recruitments:
        if rec.vacancy is not None:
            total_vacancy += rec.vacancy

    candidates = _candidates_in_period(request)
    total_candidates = candidates.count()

    hired_candidates = candidates.filter(
        Q(hired=True) | Q(stage_id__stage_type="hired")
    ).distinct()
    total_hired = hired_candidates.count()

    conversion_rate = 0
    if total_candidates > 0:
        conversion_rate = round((total_hired / total_candidates) * 100, 1)

    acceptance_rate = 0
    try:
        accepted = candidates.filter(offer_letter_status="accepted").count()
        if total_hired > 0:
            acceptance_rate = round((accepted / total_hired) * 100, 1)
    except Exception:
        pass

    onboarding_count = 0
    try:
        onboarding_count = hired_candidates.filter(start_onboard=True).count()
    except Exception:
        pass

    return JsonResponse(
        {
            "total_vacancy": total_vacancy,
            "ongoing_recruitments": ongoing,
            "total_hired": total_hired,
            "total_candidates": total_candidates,
            "conversion_rate": conversion_rate,
            "acceptance_rate": acceptance_rate,
            "onboarding_count": onboarding_count,
        }
    )


@login_required
def recruitment_offer_status(request):
    """Candidate offer letter status breakdown."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Candidate

    statuses = ["not_sent", "sent", "accepted", "rejected", "joined"]
    labels = ["Not Sent", "Sent", "Accepted", "Rejected", "Joined"]
    data = []

    base_qs = Candidate.objects.filter(is_active=True)
    for status in statuses:
        data.append(base_qs.filter(offer_letter_status=status).count())

    return JsonResponse({"labels": labels, "data": data, "statuses": statuses})


@login_required
def recruitment_stage_summary(request):
    """Candidates grouped by stage type across all active recruitments."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Candidate, Recruitment, Stage

    recruitments = Recruitment.objects.filter(closed=False)

    stages = []
    for type_key, type_label in Stage.stage_types:
        count = Candidate.objects.filter(
            stage_id__stage_type=type_key,
            is_active=True,
        ).count()
        stages.append({"type": type_key, "label": str(type_label), "count": count})

    return JsonResponse({"stages": stages})


@login_required
def recruitment_pipeline_data(request):
    """Hiring pipeline — candidates per stage per recruitment."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Recruitment, Stage

    recruitments = Recruitment.objects.filter(closed=False)
    period_candidates = _candidates_in_period(request)
    pipeline = []

    for rec in recruitments:
        rec_cands = period_candidates.filter(recruitment_id=rec)
        total = rec_cands.count()
        if not total:
            continue
        stages = {}
        for stage_type, stage_label in Stage.stage_types:
            stages[stage_type] = rec_cands.filter(
                stage_id__stage_type=stage_type
            ).count()
        pipeline.append(
            {
                "id": rec.id,
                "recruitment": rec.title or str(rec),
                "stages": stages,
                "total": total,
            }
        )

    return JsonResponse({"pipeline": pipeline, "stage_types": dict(Stage.stage_types)})


@login_required
def recruitment_source_quality(request):
    """Top recruitments by hire rate."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Recruitment

    recruitments = Recruitment.objects.filter(closed=False)
    period_candidates = _candidates_in_period(request)
    sources = []

    for rec in recruitments:
        rec_cands = period_candidates.filter(recruitment_id=rec)
        total = rec_cands.count()
        if total == 0:
            continue
        hired = (
            rec_cands.filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
            .distinct()
            .count()
        )
        rate = round((hired / total) * 100, 1) if total > 0 else 0
        sources.append(
            {
                "recruitment": rec.title or str(rec),
                "total": total,
                "hired": hired,
                "rate": rate,
            }
        )

    sources.sort(key=lambda x: x["rate"], reverse=True)

    return JsonResponse({"sources": sources[:10]})


@login_required
def recruitment_time_to_hire(request):
    """Average time from candidate creation to hired stage, per recruitment."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Recruitment

    recruitments = Recruitment.objects.filter(closed=False)
    period_candidates = _candidates_in_period(request)
    data = []

    for rec in recruitments:
        hired = (
            period_candidates.filter(recruitment_id=rec)
            .filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
            .distinct()
        )
        if not hired.exists():
            continue

        days_list = []
        for c in hired:
            if c.joining_date and c.created_at:
                try:
                    delta = (c.joining_date - c.created_at.date()).days
                    if delta >= 0:
                        days_list.append(delta)
                except Exception:
                    pass

        avg_days = round(sum(days_list) / len(days_list)) if days_list else None
        data.append(
            {
                "recruitment": rec.title or str(rec),
                "avg_days": avg_days,
                "hired_count": hired.count(),
            }
        )

    return JsonResponse({"data": [d for d in data if d["avg_days"] is not None]})


@login_required
def recruitment_managers_data(request):
    """Ongoing recruitments with their managers."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Recruitment

    recruitments = Recruitment.objects.filter(closed=False)
    period_candidates = _candidates_in_period(request)
    data = []

    for rec in recruitments:
        managers = [m.get_full_name() for m in rec.recruitment_managers.all()]
        data.append(
            {
                "recruitment": rec.title or str(rec),
                "managers": managers,
                "vacancy": rec.vacancy or 0,
                "candidates": period_candidates.filter(recruitment_id=rec).count(),
            }
        )

    return JsonResponse({"recruitments": data})


@login_required
def recruitment_upcoming_interviews(request):
    """Interviews scheduled within the selected period."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import InterviewSchedule

    from_date, to_date = _parse_period(request)
    today = date.today()
    interviews = []

    try:
        qs = (
            InterviewSchedule.objects.filter(
                interview_date__gte=from_date,
                interview_date__lte=to_date,
            )
            .select_related("candidate_id", "candidate_id__stage_id")
            .order_by("interview_date", "interview_time")[:15]
        )

        for iv in qs:
            cand = iv.candidate_id
            interviews.append(
                {
                    "id": iv.id,
                    "candidate": cand.name if cand else "—",
                    "candidate_id": cand.id if cand else None,
                    "stage": cand.stage_id.stage if cand and cand.stage_id else "—",
                    "date": iv.interview_date.strftime("%b %d"),
                    "time": (
                        iv.interview_time.strftime("%I:%M %p")
                        if iv.interview_time
                        else ""
                    ),
                    "days_away": (iv.interview_date - today).days,
                }
            )
    except Exception:
        pass

    return JsonResponse({"interviews": interviews})


@login_required
def recruitment_open_by_department(request):
    """Open positions grouped by department, scoped to recruitments active in the selected period."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from base.models import Department
    from recruitment.models import Recruitment

    from_date, to_date = _parse_period(request)
    departments = []

    try:
        recruitments = Recruitment.objects.filter(
            closed=False,
            is_event_based=False,
            start_date__lte=to_date,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=from_date))

        dept_data = {}
        for rec in recruitments:
            vacancy = rec.vacancy or 0
            filled = (
                rec.candidate.filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
                .distinct()
                .count()
            )
            open_count = max(0, vacancy - filled)

            if rec.job_position_id and rec.job_position_id.department_id:
                dept_name = rec.job_position_id.department_id.department
            else:
                dept_name = "Unassigned"

            if dept_name not in dept_data:
                dept_data[dept_name] = {"open": 0, "total_vacancy": 0, "filled": 0}
            dept_data[dept_name]["open"] += open_count
            dept_data[dept_name]["total_vacancy"] += vacancy
            dept_data[dept_name]["filled"] += filled

        for dept, counts in dept_data.items():
            if counts["total_vacancy"] > 0:
                departments.append(
                    {
                        "department": dept,
                        "open": counts["open"],
                        "filled": counts["filled"],
                        "total": counts["total_vacancy"],
                    }
                )

        departments.sort(key=lambda x: x["open"], reverse=True)
    except Exception:
        pass

    return JsonResponse({"departments": departments})


@login_required
def recruitment_stage_conversion(request):
    """Funnel conversion rates between stages."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from recruitment.models import Candidate, Stage

    conversions = []

    try:
        stage_types = [st[0] for st in Stage.stage_types]
        stage_labels = dict(Stage.stage_types)

        counts = {}
        for st in stage_types:
            counts[st] = Candidate.objects.filter(
                stage_id__stage_type=st,
                is_active=True,
            ).count()

        total = Candidate.objects.filter(
            is_active=True,
            canceled=False,
        ).count()

        for st in stage_types:
            current = counts.get(st, 0)
            rate = round((current / total * 100), 1) if total > 0 else 0
            conversions.append(
                {
                    "stage": str(stage_labels.get(st, st)),
                    "type": st,
                    "count": current,
                    "conversion_rate": rate,
                }
            )
    except Exception:
        pass

    return JsonResponse(
        {
            "conversions": conversions,
            "total_candidates": conversions[0]["count"] if conversions else 0,
        }
    )


# Chart stays readable regardless of how many Platform records a company
# has configured - beyond this many, the rest are collapsed into "Other".
PLATFORM_CONVERSION_TOP_N = 6


@login_required
def recruitment_platform_conversion(request):
    """Hire rate per candidate source (LinkedIn, Indeed, Manual Entry, etc.).
    Powers the "Source Conversion Rate" dashboard widget."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    if not _platform_conversion_widget_enabled(request):
        return JsonResponse({"disabled": True})
    from recruitment.models import Platform

    candidates = _candidates_in_period(request)

    platforms = []
    try:
        platform_ids = (
            candidates.filter(platform__isnull=False)
            .values_list("platform_id", flat=True)
            .distinct()
        )
        totals = []
        for platform in Platform.objects.filter(id__in=platform_ids):
            total = candidates.filter(platform_id=platform.id).count()
            if total == 0:
                continue
            hired = (
                candidates.filter(platform_id=platform.id)
                .filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
                .distinct()
                .count()
            )
            totals.append({"source": platform.title, "total": total, "hired": hired})

        totals.sort(key=lambda p: p["total"], reverse=True)
        top, rest = (
            totals[:PLATFORM_CONVERSION_TOP_N],
            totals[PLATFORM_CONVERSION_TOP_N:],
        )
        for p in top:
            p["rate"] = round((p["hired"] / p["total"] * 100), 1)
            platforms.append(p)

        if rest:
            rest_total = sum(p["total"] for p in rest)
            rest_hired = sum(p["hired"] for p in rest)
            platforms.append(
                {
                    "source": str(_("Other")),
                    "total": rest_total,
                    "hired": rest_hired,
                    "rate": (
                        round((rest_hired / rest_total * 100), 1)
                        if rest_total
                        else 0
                    ),
                }
            )

        # Candidates with no platform tagged at all.
        total_ns = candidates.filter(platform__isnull=True).count()
        if total_ns > 0:
            hired_ns = (
                candidates.filter(platform__isnull=True)
                .filter(Q(hired=True) | Q(stage_id__stage_type="hired"))
                .distinct()
                .count()
            )
            platforms.append(
                {
                    "source": str(_("Not Specified")),
                    "total": total_ns,
                    "hired": hired_ns,
                    "rate": round((hired_ns / total_ns * 100), 1),
                }
            )
    except Exception:
        pass
    return JsonResponse({"platforms": platforms})


@login_required
def recruitment_joinings_monthly(request):
    """Employee joinings grouped by month within the selected period."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from employee.models import EmployeeWorkInformation

    from_date, to_date = _parse_period(request)

    month_names = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    buckets = []
    cursor = date(from_date.year, from_date.month, 1)
    end_marker = date(to_date.year, to_date.month, 1)
    while cursor <= end_marker:
        buckets.append({"year": cursor.year, "month": cursor.month, "count": 0})
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    qs = EmployeeWorkInformation.objects.filter(
        date_joining__gte=from_date,
        date_joining__lte=to_date,
    )
    for info in qs:
        if not info.date_joining:
            continue
        for b in buckets:
            if (
                b["year"] == info.date_joining.year
                and b["month"] == info.date_joining.month
            ):
                b["count"] += 1
                break

    multi_year = from_date.year != to_date.year
    labels = [
        (
            f"{month_names[b['month'] - 1][:3]} {b['year']}"
            if multi_year
            else month_names[b["month"] - 1]
        )
        for b in buckets
    ]
    data = [b["count"] for b in buckets]

    return JsonResponse({"labels": labels, "data": data, "buckets": buckets})


@login_required
def recruitment_candidates_by_date(request):
    """Candidates applied, bucketed by day within the selected period."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})

    from_date, to_date = _parse_period(request)

    buckets = []
    cursor = from_date
    while cursor <= to_date:
        buckets.append({"date": cursor.isoformat(), "count": 0})
        cursor += timedelta(days=1)
    bucket_by_date = {b["date"]: b for b in buckets}

    for applied_date in _candidates_in_period(request).values_list(
        "created_at", flat=True
    ):
        if not applied_date:
            continue
        bucket = bucket_by_date.get(applied_date.date().isoformat())
        if bucket:
            bucket["count"] += 1

    labels = [date.fromisoformat(b["date"]).strftime("%b %d") for b in buckets]
    data = [b["count"] for b in buckets]

    return JsonResponse({"labels": labels, "data": data, "buckets": buckets})


def _candidate_is_assisted(candidate):
    """A candidate counts as "assisted" once there's any recruiter trace on
    them - a note, a scheduled interview, or having moved past the initial
    stage - even if one of those happened without the others. Stage
    movement alone isn't required, since a recruiter can engage (leave a
    note, book an interview) before formally advancing the stage."""
    if candidate.has_note or candidate.has_interview:
        return True
    stage_type = candidate.stage_id.stage_type if candidate.stage_id else None
    return stage_type not in (None, "initial", "applied")


@login_required
def recruitment_response_status(request):
    """Assisted vs. not-yet-assisted candidates within the period, plus a
    list of the not-yet-assisted ones so recruiters can see exactly who's
    still waiting and for how long."""
    if not _has_recruitment_permission(request):
        return JsonResponse({"no_permission": True})
    from django.db.models import Exists, OuterRef

    from recruitment.models import InterviewSchedule, StageNote

    candidates = (
        _candidates_in_period(request)
        .select_related("stage_id", "recruitment_id")
        .annotate(
            has_note=Exists(StageNote.objects.filter(candidate_id=OuterRef("pk"))),
            has_interview=Exists(
                InterviewSchedule.objects.filter(candidate_id=OuterRef("pk"))
            ),
        )
    )

    today = date.today()
    assisted_count = 0
    pending = []
    for candidate in candidates:
        if _candidate_is_assisted(candidate):
            assisted_count += 1
        else:
            applied_date = candidate.created_at.date() if candidate.created_at else None
            pending.append(
                {
                    "id": candidate.id,
                    "name": str(candidate),
                    "applied_on": candidate.applied_on_display(),
                    "days_waiting": (
                        (today - applied_date).days if applied_date else None
                    ),
                    "recruitment": (
                        str(candidate.recruitment_id)
                        if candidate.recruitment_id
                        else ""
                    ),
                    "profile_url": str(candidate.get_profile_url()),
                    "_sort_key": applied_date or today,
                }
            )

    pending.sort(key=lambda c: c["_sort_key"])
    for row in pending:
        del row["_sort_key"]

    return JsonResponse(
        {
            "assisted": assisted_count,
            "not_assisted": len(pending),
            "total": assisted_count + len(pending),
            "pending": pending[:50],
        }
    )
