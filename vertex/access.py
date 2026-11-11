"""
vertex/access.py

Who may see or manage which Vertex project. Permissions are ordinary Ariel HR
(Django) permissions, so groups, users and the admin work as everywhere else. A
superuser (Admin) always passes.

Rules, ported from ArielVertex ProjectAccessService:

* see the Projects pages      -> vertex.view_project
* see EVERY project           -> vertex.view_allprojects (else only projects the user is on)
* manage a project            -> vertex.change_allprojects, or vertex.change_project AND
                                 being project manager / coordinator on that project
* business people on a project never see Internal documents
"""

from django.db.models import Q

from vertex.models import Project, ProjectMember, ProjectRole, Visibility

MANAGER_ROLES = (ProjectRole.PROJECT_MANAGER, ProjectRole.PROJECT_COORDINATOR)


def employee_of(user):
    return getattr(user, "employee_get", None)


def sees_all_projects(user):
    return user.has_perm("vertex.view_allprojects")


def membership(user, project):
    employee = employee_of(user)
    if employee is None:
        return None
    return ProjectMember.objects.filter(
        project=project, employee=employee, is_active=True
    ).first()


def visible_projects(user):
    """Projects the user may open."""
    if not user.has_perm("vertex.view_project"):
        return Project.objects.none()
    if sees_all_projects(user):
        return Project.objects.all()
    employee = employee_of(user)
    if employee is None:
        return Project.objects.none()
    return Project.objects.filter(
        Q(members__employee=employee, members__is_active=True)
    ).distinct()


def can_view(user, project):
    return visible_projects(user).filter(pk=project.pk).exists()


def my_role(user, project):
    member = membership(user, project)
    return member.role_on_project if member else None


def can_manage(user, project):
    if user.has_perm("vertex.change_allprojects"):
        return True
    if not user.has_perm("vertex.change_project"):
        return False
    return my_role(user, project) in MANAGER_ROLES


def is_business_only(user, project):
    """A business person on the project (without see-all) is kept away from internal items."""
    return (
        my_role(user, project) == ProjectRole.BUSINESS_PERSON
        and not sees_all_projects(user)
    )


def visible_documents(user, project):
    documents = project.documents.select_related("uploaded_by")
    if is_business_only(user, project):
        documents = documents.exclude(visibility=Visibility.INTERNAL)
    return documents


def can_open_document(user, document):
    """Rule for /media/: the file of a document opens only for people who can see it."""
    if not can_view(user, document.project):
        return False
    return not (
        is_business_only(user, document.project)
        and document.visibility == Visibility.INTERNAL
    )


# ---------------------------------------------------------------------------
# Reviews (ArielVertex ReviewsController)
# ---------------------------------------------------------------------------


def sees_all_reviews(user):
    """Requesters and holders of the see-all switch see every review request."""
    return user.has_perm("vertex.view_allreviewrequests") or user.has_perm(
        "vertex.add_reviewrequest"
    )


def visible_reviews(user):
    from vertex.models import ReviewRequest

    if not user.has_perm("vertex.view_reviewrequest"):
        return ReviewRequest.objects.none()
    reviews = ReviewRequest.objects.select_related(
        "project", "subject", "requested_by", "assigned_to"
    )
    if sees_all_reviews(user):
        return reviews
    employee = employee_of(user)
    if employee is None:
        return reviews.none()
    return reviews.filter(
        Q(assigned_to=employee) | Q(subject=employee) | Q(requested_by=employee)
    )


def can_schedule_review(user, review):
    return user.has_perm("vertex.change_reviewrequest") and can_manage(user, review.project)


def can_submit_review(user, review):
    """Only the assigned reviewer, or someone who sees every review, may submit the outcome."""
    perm = (
        "vertex.add_codereview"
        if review.review_type == "code_review"
        else "vertex.add_projectreview"
    )
    if not user.has_perm(perm):
        return False
    employee = employee_of(user)
    return (
        employee is not None and review.assigned_to_id == employee.pk
    ) or user.has_perm("vertex.view_allreviewrequests")


# ---------------------------------------------------------------------------
# Status updates (ArielVertex StatusUpdatesController)
# ---------------------------------------------------------------------------


def assignable_projects(user):
    """Projects the person may submit a status update for: those they are an active member of."""
    employee = employee_of(user)
    if employee is None:
        return Project.objects.none()
    return Project.objects.filter(
        members__employee=employee, members__is_active=True
    ).distinct()


def can_see_internal_status(user, project):
    """Internal notes and the consolidated summary: see-all holders and the project managers."""
    return user.has_perm("vertex.view_allstatusupdates") or can_manage(user, project)


def visible_status_updates(user, project):
    """
    Updates of a project as the user may see them. Business people get only a business-safe
    projection (see ``business_view``); everyone else sees the full update.
    """
    if not user.has_perm("vertex.view_statusupdate"):
        return project.status_updates.none()
    return project.status_updates.select_related("employee", "project")


def business_view(user, project):
    """True when status updates must be shown stripped (no internal note, blockers or detail)."""
    return is_business_only(user, project) and not can_see_internal_status(user, project)


def consolidated_summary(project, days=7):
    """
    Business-ready summary of the last ``days`` days: the client-shareable updates, and who
    of the developers / QA on the project has not reported (ArielVertex Consolidated).
    """
    from datetime import timedelta

    from django.utils import timezone

    days = max(1, min(int(days), 60))
    start = timezone.localdate() - timedelta(days=days)
    updates = list(
        project.status_updates.filter(update_date__gte=start).select_related("employee")
    )
    expected = list(
        project.members.filter(
            is_active=True, role_on_project__in=[ProjectRole.DEVELOPER, ProjectRole.QA]
        ).select_related("employee")
    )
    reporting_ids = {u.employee_id for u in updates}
    return {
        "days": days,
        "start": start,
        "updates": updates,
        "reporting": len(reporting_ids),
        "expected": len(expected),
        "missing": [m.employee.get_full_name() for m in expected if m.employee_id not in reporting_ids],
    }
