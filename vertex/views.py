"""
vertex/views.py

Vertex Projects: list, create, edit and the project workspace (overview, team,
documents, calls, comments). All access goes through vertex.access.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access
from vertex.forms import (
    CallForm,
    CommentForm,
    DocumentForm,
    MemberForm,
    ProjectForm,
)
from vertex.models import (
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
)

TABS = ("overview", "team", "documents", "calls", "comments")


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
def project_list(request):
    projects = access.visible_projects(request.user).prefetch_related("members")
    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "")
    if search:
        projects = projects.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(client_name__icontains=search)
        )
    if status in ProjectStatus.values:
        projects = projects.filter(status=status)
    page = Paginator(projects.order_by("-priority", "name"), 20).get_page(
        request.GET.get("page")
    )
    employee = access.employee_of(request.user)
    my_roles = {}
    if employee is not None:
        my_roles = {
            m.project_id: m.get_role_on_project_display()
            for m in ProjectMember.objects.filter(
                employee=employee, is_active=True, project__in=list(page)
            )
        }
    return render(
        request,
        "vertex/project_list.html",
        {
            "page": page,
            "search": search,
            "status": status,
            "statuses": ProjectStatus.choices,
            "my_roles": my_roles,
            "can_create": request.user.has_perm("vertex.add_project"),
        },
    )


@login_required
@permission_required("vertex.add_project")
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False)
        employee = access.employee_of(request.user)
        project.created_by = employee
        project.save()
        if employee is not None:
            # the creator becomes PM so they keep manage access, as in ArielVertex
            ProjectMember.objects.create(
                project=project,
                employee=employee,
                role_on_project=ProjectRole.PROJECT_MANAGER,
                allocation_pct=0,
                start_date=project.start_date,
            )
        messages.success(request, _("Project created."))
        return redirect(project.get_absolute_url())
    return render(
        request, "vertex/project_form.html", {"form": form, "title": _("New project")}
    )


@login_required
@permission_required("vertex.view_project")
def project_edit(request, pk):
    project = _project_or_404(request, pk)
    if not access.can_manage(request.user, project):
        return handle_no_permission(request)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Project updated."))
        return redirect(project.get_absolute_url())
    return render(
        request,
        "vertex/project_form.html",
        {"form": form, "title": _("Edit project"), "project": project},
    )


@login_required
@permission_required("vertex.view_project")
def project_detail(request, pk):
    project = _project_or_404(request, pk)
    tab = request.GET.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    can_manage = access.can_manage(request.user, project)
    context = {
        "project": project,
        "tab": tab,
        "tabs": TABS,
        "can_manage": can_manage,
        "my_role": access.my_role(request.user, project),
        "members": project.members.filter(is_active=True).select_related("employee"),
        "documents": access.visible_documents(request.user, project),
        "calls": project.calls.all(),
        "comments": project.comments.select_related("author"),
        "member_form": MemberForm(),
        "document_form": DocumentForm(),
        "call_form": CallForm(),
        "comment_form": CommentForm(),
        "can_add_member": can_manage and request.user.has_perm("vertex.add_projectmember"),
        "can_remove_member": can_manage and request.user.has_perm("vertex.delete_projectmember"),
        "can_add_document": request.user.has_perm("vertex.add_projectdocument"),
        "can_delete_document": can_manage and request.user.has_perm("vertex.delete_projectdocument"),
        "can_add_call": can_manage and request.user.has_perm("vertex.add_projectcall"),
    }
    return render(request, "vertex/project_detail.html", context)


@login_required
@permission_required("vertex.add_projectmember")
@post_only
def member_add(request, pk):
    project = _project_or_404(request, pk)
    if not access.can_manage(request.user, project):
        return handle_no_permission(request)
    form = MemberForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return _back(project, "team")
    data = form.cleaned_data
    # membership history is kept on removal; adding again reactivates the same row
    member = ProjectMember.objects.filter(project=project, employee=data["employee"]).first()
    if member and member.is_active:
        messages.error(request, _("That person is already on the project."))
        return _back(project, "team")
    member = member or ProjectMember(project=project, employee=data["employee"])
    member.role_on_project = data["role_on_project"]
    member.allocation_pct = data["allocation_pct"]
    member.start_date = data["start_date"]
    member.end_date = data["end_date"]
    member.is_active = True
    member.save()
    messages.success(request, _("Team member added."))
    return _back(project, "team")


@login_required
@permission_required("vertex.delete_projectmember")
@post_only
def member_remove(request, pk, member_id):
    from django.utils import timezone

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
@permission_required("vertex.add_projectdocument")
@post_only
def document_add(request, pk):
    project = _project_or_404(request, pk)
    form = DocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return _back(project, "documents")
    document = form.save(commit=False)
    document.project = project
    document.uploaded_by = access.employee_of(request.user)
    if document.file:
        document.file_name = request.FILES["file"].name[:255]
        document.size_bytes = request.FILES["file"].size
    document.save()
    messages.success(request, _("Document added."))
    return _back(project, "documents")


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


@login_required
@permission_required("vertex.add_projectcall")
@post_only
def call_add(request, pk):
    project = _project_or_404(request, pk)
    if not access.can_manage(request.user, project):
        return handle_no_permission(request)
    form = CallForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return _back(project, "calls")
    call = form.save(commit=False)
    call.project = project
    call.scheduled_by = access.employee_of(request.user)
    call.save()
    messages.success(request, _("Call scheduled."))
    return _back(project, "calls")
