"""
vertex/views.py

The Vertex project workspace (overview, team, documents, calls, status updates, reviews,
comments) and its plain actions (remove member, post comment, download / delete document).
The list, the create / edit form and the add-member / add-document / add-call modals are
Ariel HR framework views in vertex/cbv.py. All access goes through vertex.access.
"""

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access
from vertex.forms import CommentForm
from vertex.models import ProjectMember, ProjectRole

TABS = ("overview", "team", "documents", "calls", "status", "reviews", "comments")


def _project_or_404(request, pk):
    """A project the user may open; anything else is a 404 (no hint that it exists)."""
    project = access.visible_projects(request.user).filter(pk=pk).first()
    if project is None:
        raise Http404
    return project


def _back(project, tab):
    return redirect(f"{project.get_absolute_url()}?tab={tab}")


@login_required
@permission_required("vertex.view_project")
def project_detail(request, pk):
    project = _project_or_404(request, pk)
    user = request.user
    tab = request.GET.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    can_manage = access.can_manage(user, project)
    members = list(project.members.filter(is_active=True).select_related("employee"))
    context = {
        "project": project,
        "tab": tab,
        "tabs": TABS,
        "can_manage": can_manage,
        "my_role": access.my_role(user, project),
        "my_role_label": (
            ProjectRole(access.my_role(user, project)).label
            if access.my_role(user, project)
            else ""
        ),
        "members": members,
        "can_add_member": can_manage and user.has_perm("vertex.add_projectmember"),
        "can_remove_member": can_manage and user.has_perm("vertex.delete_projectmember"),
        "can_add_document": user.has_perm("vertex.add_projectdocument"),
        "can_delete_document": can_manage and user.has_perm("vertex.delete_projectdocument"),
        "can_add_call": can_manage and user.has_perm("vertex.add_projectcall"),
        "can_submit_status": user.has_perm("vertex.add_statusupdate")
        and access.assignable_projects(user).filter(pk=project.pk).exists(),
        "can_request_review": user.has_perm("vertex.add_reviewrequest"),
        "can_request_resource": user.has_perm("vertex.add_resourcerequest"),
        "comment_form": CommentForm(),
        "counts": {"team": len(members)},
    }
    # only the tab that is open is loaded
    if tab == "documents":
        context["documents"] = access.visible_documents(user, project)
    elif tab == "calls":
        context["calls"] = project.calls.all()
    elif tab == "comments":
        context["comments"] = project.comments.select_related("author")
        context["my_employee"] = access.employee_of(user)
    elif tab == "reviews":
        context["reviews"] = access.visible_reviews(user).filter(project=project)
    elif tab == "status":
        can_see_internal = access.can_see_internal_status(user, project)
        context["status_can_see_internal"] = can_see_internal
        context["status_business_view"] = access.business_view(user, project)
        context["status_updates"] = access.visible_status_updates(user, project)[:100]
        if can_see_internal:
            try:
                days = int(request.GET.get("days", 7))
            except ValueError:
                days = 7
            context["summary"] = access.consolidated_summary(project, days)
    return render(request, "vertex/project_detail.html", context)


@login_required
@permission_required("vertex.delete_projectmember")
@post_only
def member_remove(request, pk, member_id):
    project = _project_or_404(request, pk)
    if not access.can_manage(request.user, project):
        return handle_no_permission(request)
    member = get_object_or_404(ProjectMember, pk=member_id, project=project)
    member.is_active = False
    member.end_date = timezone.localdate()
    member.save(update_fields=["is_active", "end_date"])
    messages.success(request, _("Team member removed."))
    return _back(project, "team")


@login_required
@permission_required("vertex.view_project")
@post_only
def comment_add(request, pk):
    project = _project_or_404(request, pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.project = project
        comment.author = access.employee_of(request.user)
        comment.save()
    else:
        messages.error(request, _("Comment cannot be empty."))
    return _back(project, "comments")


@login_required
@permission_required("vertex.view_project")
def document_download(request, pk, doc_id):
    project = _project_or_404(request, pk)
    document = get_object_or_404(
        access.visible_documents(request.user, project), pk=doc_id
    )
    if document.is_video_link or not document.file:
        raise Http404
    response = FileResponse(
        document.file.open("rb"),
        as_attachment=True,
        filename=document.file_name or document.file.name.rsplit("/", 1)[-1],
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@permission_required("vertex.delete_projectdocument")
@post_only
def document_delete(request, pk, doc_id):
    project = _project_or_404(request, pk)
    if not access.can_manage(request.user, project):
        return handle_no_permission(request)
    get_object_or_404(project.documents, pk=doc_id).delete()
    messages.success(request, _("Document removed."))
    return _back(project, "documents")
