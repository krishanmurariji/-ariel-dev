"""
vertex/views_resources.py

Resource request detail (status flow + comment thread) and the Resource Visibility page
(ArielVertex ResourceRequestsController and ResourcesController).
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access
from vertex.forms import ResourceCommentForm, ResourceStatusForm
from vertex.models import ResourceRequestComment, ResourceRequestStatus
from vertex.notify import employees_with_perm, notify_employees


def _request_or_404(request, pk):
    resource_request = access.visible_resource_requests(request.user).filter(pk=pk).first()
    if resource_request is None:
        raise Http404
    return resource_request


@login_required
@permission_required("vertex.view_resourcerequest")
def resource_request_detail(request, pk):
    resource_request = _request_or_404(request, pk)
    can_change = request.user.has_perm("vertex.change_resourcerequest")
    return render(
        request,
        "vertex/resource_request_detail.html",
        {
            "rr": resource_request,
            "comments": resource_request.comments.select_related("author"),
            "can_change": can_change,
            "status_form": ResourceStatusForm(initial={"status": resource_request.status}),
            "comment_form": ResourceCommentForm(),
            "my_employee": access.employee_of(request.user),
        },
    )


@login_required
@permission_required("vertex.change_resourcerequest")
@post_only
def resource_request_status(request, pk):
    resource_request = _request_or_404(request, pk)
    form = ResourceStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Choose a valid status."))
        return redirect(resource_request.get_absolute_url())
    me = access.employee_of(request.user)
    status = form.cleaned_data["status"]
    comment = form.cleaned_data["comment"].strip()
    changed = status != resource_request.status
    if changed:
        resource_request.status = status
        resource_request.save(update_fields=["status", "updated_at"])
    if comment:
        ResourceRequestComment.objects.create(
            resource_request=resource_request, author=me, message=comment
        )
    if changed or comment:
        notify_employees(
            me,
            [resource_request.requested_by],
            str(_("Your resource request '%(role)s' is now: %(status)s."))
            % {
                "role": resource_request.role_title,
                "status": ResourceRequestStatus(resource_request.status).label,
            },
            resource_request.get_absolute_url(),
        )
    messages.success(request, _("Request updated."))
    return redirect(resource_request.get_absolute_url())


@login_required
@permission_required("vertex.view_resourcerequest")
@post_only
def resource_request_comment(request, pk):
    resource_request = _request_or_404(request, pk)
    form = ResourceCommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Comment cannot be empty."))
        return redirect(resource_request.get_absolute_url())
    me = access.employee_of(request.user)
    comment = form.save(commit=False)
    comment.resource_request = resource_request
    comment.author = me
    comment.save()
    # the requester and HR talk to each other: tell the other side
    if me is not None and me == resource_request.requested_by:
        recipients = employees_with_perm("vertex.change_resourcerequest")
    else:
        recipients = [resource_request.requested_by]
    notify_employees(
        me,
        recipients,
        str(_("New comment on the resource request '%(role)s'."))
        % {"role": resource_request.role_title},
        resource_request.get_absolute_url(),
    )
    return redirect(resource_request.get_absolute_url())


@login_required
@permission_required("vertex.view_resourcevisibility")
def resource_visibility(request):
    search = request.GET.get("search", "").strip()
    availability = request.GET.get("availability", "")
    rows, buckets = access.occupancy(search)
    if availability in access.AVAILABILITY_ORDER:
        rows = [r for r in rows if r["availability"] == availability]
    return render(
        request,
        "vertex/resource_visibility.html",
        {
            "rows": rows,
            "buckets": buckets,
            "search": search,
            "availability": availability,
            "availabilities": access.AVAILABILITY_ORDER,
        },
    )
