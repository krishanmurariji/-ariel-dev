"""
vertex/views_reviews.py

Review requests (ArielVertex ReviewsController): request for one or many employees,
schedule, the subject's accept / decline, and the reviewer's submitted outcome.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access
from vertex.forms_reviews import (
    CodeReviewForm,
    ProjectReviewForm,
    ReviewRequestForm,
    ScheduleForm,
)
from vertex.minutes import split_emails
from vertex.models import (
    CodeReview,
    MeetingResponse,
    ProjectReview,
    ReviewMeeting,
    ReviewRequest,
    ReviewRequestStatus,
)
from vertex.notify import notify_employees


def _review_or_404(request, pk):
    review = access.visible_reviews(request.user).filter(pk=pk).first()
    if review is None:
        raise Http404
    return review


def _attendees(subjects, assignee, me, extra):
    emails = [p.email for p in [*subjects, assignee, me] if p is not None and p.email]
    return ", ".join(dict.fromkeys(e.lower() for e in [*emails, *split_emails(extra)]))


@login_required
@permission_required("vertex.view_reviewrequest")
def review_list(request):
    reviews = access.visible_reviews(request.user)
    status = request.GET.get("status", "")
    if status in ReviewRequestStatus.values:
        reviews = reviews.filter(status=status)
    return render(
        request,
        "vertex/review_list.html",
        {
            "reviews": reviews.select_related("meeting")[:200],
            "status": status,
            "statuses": ReviewRequestStatus.choices,
            "can_request": request.user.has_perm("vertex.add_reviewrequest"),
        },
    )


@login_required
@permission_required("vertex.add_reviewrequest")
def review_create(request):
    form = ReviewRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        me = access.employee_of(request.user)
        subjects = list(data["subjects"])
        assignee = data["assigned_to"]
        scheduled_at = data["scheduled_at"]
        attendees = _attendees(subjects, assignee, me, data["extra_attendees"])
        for subject in subjects:
            review = ReviewRequest.objects.create(
                project=data["project"],
                subject=subject,
                requested_by=me,
                assigned_to=assignee,
                review_type=data["review_type"],
                notes=data["notes"],
                due_date=data["due_date"],
                status=(
                    ReviewRequestStatus.SCHEDULED if scheduled_at else ReviewRequestStatus.OPEN
                ),
            )
            if scheduled_at:
                ReviewMeeting.objects.create(
                    review_request=review,
                    title=data["title"],
                    scheduled_at=scheduled_at,
                    duration_minutes=data["duration_minutes"] or 30,
                    attendees=attendees,
                    meeting_url=data["meeting_url"],
                    scheduled_by=me,
                )
        verb = (
            _("A review has been scheduled for you.")
            if scheduled_at
            else _("A review has been requested for you.")
        )
        notify_employees(me, subjects, str(verb), "/vertex/reviews/")
        if assignee:
            notify_employees(me, [assignee], str(_("You have been asked to run a review.")), "/vertex/reviews/")
        messages.success(request, _("%(n)d review(s) created.") % {"n": len(subjects)})
        return redirect("vertex-reviews")
    return render(request, "vertex/review_form.html", {"form": form})


@login_required
@permission_required("vertex.view_reviewrequest")
def review_detail(request, pk):
    review = _review_or_404(request, pk)
    employee = access.employee_of(request.user)
    meeting = getattr(review, "meeting", None)
    can_submit = access.can_submit_review(request.user, review)
    is_code = review.review_type == "code_review"
    submitted = getattr(review, "code_review" if is_code else "project_review", None)
    form_class = CodeReviewForm if is_code else ProjectReviewForm
    return render(
        request,
        "vertex/review_detail.html",
        {
            "review": review,
            "meeting": meeting,
            "can_schedule": access.can_schedule_review(request.user, review)
            and review.status != ReviewRequestStatus.COMPLETED,
            "schedule_form": ScheduleForm(
                initial={
                    "title": meeting.title if meeting else "",
                    "assigned_to": review.assigned_to,
                }
            ),
            "can_respond": bool(
                employee and meeting and review.subject_id == employee.pk
            ),
            "responses": MeetingResponse.choices,
            "can_submit": can_submit and review.status != ReviewRequestStatus.CANCELLED,
            "submit_form": form_class(instance=submitted),
            "submitted": submitted,
            "is_code": is_code,
        },
    )


@login_required
@permission_required("vertex.change_reviewrequest")
@post_only
def review_schedule(request, pk):
    review = _review_or_404(request, pk)
    if not access.can_schedule_review(request.user, review):
        return handle_no_permission(request)
    form = ScheduleForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect(review.get_absolute_url())
    data = form.cleaned_data
    me = access.employee_of(request.user)
    assignee = data["assigned_to"] or review.assigned_to
    meeting = getattr(review, "meeting", None) or ReviewMeeting(review_request=review)
    meeting.title = data["title"]
    meeting.description = data["description"]
    meeting.scheduled_at = data["scheduled_at"]
    meeting.duration_minutes = data["duration_minutes"]
    meeting.meeting_url = data["meeting_url"]
    meeting.attendees = _attendees([review.subject], assignee, me, data["extra_attendees"])
    meeting.scheduled_by = me
    meeting.save()
    if data["assigned_to"]:
        review.assigned_to = data["assigned_to"]
    review.status = ReviewRequestStatus.SCHEDULED
    review.save(update_fields=["assigned_to", "status"])
    notify_employees(
        me, [review.subject, assignee], str(_("A review has been scheduled.")), review.get_absolute_url()
    )
    messages.success(request, _("Review scheduled."))
    return redirect(review.get_absolute_url())


@login_required
@permission_required("vertex.view_reviewrequest")
@post_only
def review_respond(request, pk):
    review = _review_or_404(request, pk)
    employee = access.employee_of(request.user)
    meeting = getattr(review, "meeting", None)
    if employee is None or review.subject_id != employee.pk:
        return handle_no_permission(request, _("Only the person being reviewed can respond."))
    if meeting is None:
        messages.error(request, _("This review has not been scheduled yet."))
        return redirect(review.get_absolute_url())
    response = request.POST.get("response", "")
    if response not in (MeetingResponse.ACCEPTED, MeetingResponse.DECLINED, MeetingResponse.TENTATIVE):
        messages.error(request, _("Choose accepted, declined or tentative."))
        return redirect(review.get_absolute_url())
    meeting.response_status = response
    meeting.responded_at = timezone.now()
    meeting.save(update_fields=["response_status", "responded_at"])
    notify_employees(
        employee,
        [review.requested_by],
        str(_("%(name)s responded '%(response)s' to the '%(title)s' review."))
        % {
            "name": employee.get_full_name(),
            "response": meeting.get_response_status_display(),
            "title": meeting.title,
        },
        review.get_absolute_url(),
    )
    messages.success(request, _("Your response was saved."))
    return redirect(review.get_absolute_url())


@login_required
@permission_required("vertex.view_reviewrequest")
@post_only
def review_submit(request, pk):
    review = _review_or_404(request, pk)
    if not access.can_submit_review(request.user, review):
        return handle_no_permission(request)
    if review.status == ReviewRequestStatus.CANCELLED:
        raise Http404
    is_code = review.review_type == "code_review"
    model = CodeReview if is_code else ProjectReview
    form_class = CodeReviewForm if is_code else ProjectReviewForm
    instance = model.objects.filter(review_request=review).first()
    form = form_class(request.POST, instance=instance)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect(review.get_absolute_url())
    outcome = form.save(commit=False)
    outcome.review_request = review
    if instance is None:
        outcome.reviewer = access.employee_of(request.user)
    outcome.save()
    review.status = ReviewRequestStatus.COMPLETED
    review.closed_at = timezone.now()
    review.save(update_fields=["status", "closed_at"])
    notify_employees(
        access.employee_of(request.user),
        [review.requested_by],
        str(_("A requested review outcome has been submitted.")),
        review.get_absolute_url(),
    )
    messages.success(request, _("Review submitted."))
    return redirect(review.get_absolute_url())
