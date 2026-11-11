"""
vertex/cbv.py

Vertex Projects built on Ariel HR's own list / card / filter / modal framework
(horilla_views), so they look and behave like every other Ariel HR page:
nav bar (search, filter, group by, list/card toggle, create button), list table,
card grid and a modal create / edit form.
"""

from typing import Any

from django.contrib import messages
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from horilla.methods import handle_no_permission
from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaCardView,
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from vertex import access
from vertex.filters import (
    ResourceRequestFilter,
    StatusUpdateFilter,
    VertexProjectFilter,
)
from vertex.forms import (
    CallForm,
    DocumentForm,
    MemberForm,
    ProjectForm,
    ResourceRequestForm,
    StatusUpdateForm,
)
from vertex.models import (
    Project,
    ProjectCall,
    ProjectDocument,
    ProjectMember,
    ProjectRole,
    ResourceRequest,
    StatusUpdate,
)
from vertex.notify import employees_with_perm, notify_employees


def _status_filter_js(value):
    return f"""
        onclick="
            $('#applyFilter').closest('form').find('[name=status]').val('{value}');
            $('#applyFilter').click();
        "
    """


STATUS_INDICATIONS = [
    ("planning--dot", _("Planning"), _status_filter_js("planning")),
    ("active--dot", _("Active"), _status_filter_js("active")),
    ("on-hold--dot", _("On hold"), _status_filter_js("on_hold")),
    ("completed--dot", _("Completed"), _status_filter_js("completed")),
    ("cancelled--dot", _("Cancelled"), _status_filter_js("cancelled")),
]


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_project"), name="dispatch")
class ProjectsView(TemplateView):
    template_name = "vertex/cbv/projects.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_project"), name="dispatch")
class ProjectsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = VertexProjectFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status", "health", "priority"]
    filter_body_template = "vertex/cbv/project_filter.html"
    hide_actions_button = True
    create_label = _("New project")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-project-list")
        self.view_types = [
            {
                "type": "list",
                "icon": "list-outline",
                "url": reverse("vertex-project-list"),
                "attrs": f"""title ='{_("List")}'""",
            },
            {
                "type": "card",
                "icon": "grid-outline",
                "url": reverse("vertex-project-card"),
                "attrs": f"""title ='{_("Card")}'""",
            },
        ]
        if self.request.user.has_perm("vertex.add_project"):
            self.create_attrs = f"""
                onclick = "event.stopPropagation();"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                hx-get="{reverse('vertex-project-new')}"
            """


def _visible_ids(view):
    return access.visible_projects(view.request.user).values("pk")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_project"), name="dispatch")
class ProjectsList(HorillaListView):
    model = Project
    filter_class = VertexProjectFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=_visible_ids(self)).prefetch_related(
            "members"
        ).order_by("-priority", "name")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-project-list")

    @cached_property
    def columns(self):
        get_field = self.model()._meta.get_field
        return [
            (get_field("code").verbose_name, "code"),
            (get_field("name").verbose_name, "name"),
            (_("Client"), "client_name"),
            (get_field("status").verbose_name, "get_status_display"),
            (_("Health"), "get_health_html"),
            (get_field("priority").verbose_name, "get_priority_display"),
            (_("Team"), "get_member_count"),
            (get_field("start_date").verbose_name, "start_date"),
            (get_field("expected_end_date").verbose_name, "expected_end_date"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [
            (get_field("code").verbose_name, "code"),
            (get_field("name").verbose_name, "name"),
            (get_field("priority").verbose_name, "priority"),
            (get_field("start_date").verbose_name, "start_date"),
        ]

    row_status_indications = STATUS_INDICATIONS
    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_project"), name="dispatch")
class ProjectsCard(HorillaCardView):
    model = Project
    filter_class = VertexProjectFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=_visible_ids(self)).prefetch_related(
            "members"
        ).order_by("-priority", "name")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-project-card")

    details = {
        "image_src": "get_avatar",
        "title": "{code} · {name}",
        "subtitle": "{get_card_view_subtitle}",
    }
    card_attrs = """ {redirect} """
    card_status_class = "status-{status}"
    card_status_indications = STATUS_INDICATIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        self.actions = [
            {
                "action": _("Open"),
                "attrs": """onclick="window.location.href='{get_absolute_url}'" class="oh-dropdown__link" """,
            },
            {
                "action": _("Edit"),
                "accessibility": "vertex.cbv.can_edit_project",
                "attrs": """
                    hx-get='{get_update_url}'
                    hx-target='#genericModalBody'
                    data-toggle="oh-modal-toggle"
                    data-target="#genericModal"
                    class="oh-dropdown__link"
                """,
            },
        ]
        context["actions"] = self.actions
        return context


def can_edit_project(request, instance, *args, **kwargs):
    """Accessibility hook for the card / row actions."""
    return access.can_manage(request.user, instance)


@method_decorator(login_required, name="dispatch")
class ProjectFormView(HorillaFormView):
    """Modal form: create a project (add_project) or edit one the user manages."""

    model = Project
    form_class = ProjectForm
    new_display_title = _("New project")

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        if pk:
            project = access.visible_projects(request.user).filter(pk=pk).first()
            if project is None or not access.can_manage(request.user, project):
                return handle_no_permission(request)
        elif not request.user.has_perm("vertex.add_project"):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Edit project")
        return context

    def form_valid(self, form):
        creating = form.instance.pk is None
        employee = access.employee_of(self.request.user)
        project = form.save(commit=False)
        if creating:
            project.created_by = employee
        project.save()
        if creating and employee is not None:
            # the creator becomes PM so they keep manage access, as in ArielVertex
            ProjectMember.objects.create(
                project=project,
                employee=employee,
                role_on_project=ProjectRole.PROJECT_MANAGER,
                allocation_pct=0,
                start_date=project.start_date,
            )
        messages.success(
            self.request, _("Project created.") if creating else _("Project updated.")
        )
        if not creating and "/vertex/projects/" in self.request.META.get("HTTP_REFERER", ""):
            return self.HttpResponse("<script>window.location.reload()</script>")
        return self.HttpResponse()


# ---------------------------------------------------------------------------
# Project workspace modals: team member, document, call
# ---------------------------------------------------------------------------

RELOAD_PAGE = "<script>window.location.reload()</script>"


@method_decorator(login_required, name="dispatch")
class _ProjectChildFormView(HorillaFormView):
    """
    Base of the workspace modals. The project comes from the URL (``project_id``); the user
    must see the project, hold ``required_perm`` and, when ``need_manage``, manage it (PM / PC).
    """

    required_perm = ""
    need_manage = True

    def dispatch(self, request, *args, **kwargs):
        project = access.visible_projects(request.user).filter(
            pk=kwargs.get("project_id")
        ).first()
        if (
            project is None
            or not request.user.has_perm(self.required_perm)
            or (self.need_manage and not access.can_manage(request.user, project))
        ):
            return handle_no_permission(request)
        self.project = project
        return super().dispatch(request, *args, **kwargs)

    def saved(self, message):
        messages.success(self.request, message)
        return self.HttpResponse(RELOAD_PAGE)


class ProjectMemberFormView(_ProjectChildFormView):
    model = ProjectMember
    form_class = MemberForm
    new_display_title = _("Add team member")
    required_perm = "vertex.add_projectmember"

    def form_valid(self, form):
        data = form.cleaned_data
        # membership history is kept on removal; adding again reactivates the same row
        member = ProjectMember.objects.filter(
            project=self.project, employee=data["employee"]
        ).first()
        if member and member.is_active:
            form.add_error("employee", _("That person is already on the project."))
            return self.form_invalid(form)
        member = member or ProjectMember(project=self.project, employee=data["employee"])
        member.role_on_project = data["role_on_project"]
        member.allocation_pct = data["allocation_pct"]
        member.start_date = data["start_date"]
        member.end_date = data["end_date"]
        member.is_active = True
        member.save()
        notify_employees(
            access.employee_of(self.request.user),
            [member.employee],
            str(_("You have been added to the project %(name)s."))
            % {"name": self.project.name},
            self.project.get_absolute_url(),
        )
        return self.saved(_("Team member added."))


class ProjectDocumentFormView(_ProjectChildFormView):
    model = ProjectDocument
    form_class = DocumentForm
    new_display_title = _("Add document")
    required_perm = "vertex.add_projectdocument"
    need_manage = False  # any member who holds the permission, as ArielVertex documents.upload

    def form_valid(self, form):
        document = form.save(commit=False)
        document.project = self.project
        document.uploaded_by = access.employee_of(self.request.user)
        upload = self.request.FILES.get("file")
        if document.file and upload:
            document.file_name = upload.name[:255]
            document.size_bytes = upload.size
        document.save()
        return self.saved(_("Document added."))


class ProjectCallFormView(_ProjectChildFormView):
    model = ProjectCall
    form_class = CallForm
    new_display_title = _("Schedule call")
    required_perm = "vertex.add_projectcall"

    def form_valid(self, form):
        call = form.save(commit=False)
        call.project = self.project
        call.scheduled_by = access.employee_of(self.request.user)
        call.save()
        return self.saved(_("Call scheduled."))


# ---------------------------------------------------------------------------
# Status updates page: my updates, with a modal to submit a new one
# ---------------------------------------------------------------------------


def _status_indications():
    def js(value):
        return f"""
            onclick="
                $('#applyFilter').closest('form').find('[name=status]').val('{value}');
                $('#applyFilter').click();
            "
        """

    return [
        ("on_track--dot", _("On track"), js("on_track")),
        ("at_risk--dot", _("At risk"), js("at_risk")),
        ("blocked--dot", _("Blocked"), js("blocked")),
    ]


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_statusupdate"), name="dispatch")
class StatusUpdatesView(TemplateView):
    template_name = "vertex/cbv/status_updates.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_statusupdate"), name="dispatch")
class StatusUpdatesNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = StatusUpdateFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["project", "status"]
    filter_body_template = "vertex/cbv/status_filter.html"
    hide_actions_button = True
    create_label = _("New update")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-status-list")
        user = self.request.user
        if user.has_perm("vertex.add_statusupdate") and access.assignable_projects(user).exists():
            self.create_attrs = f"""
                onclick = "event.stopPropagation();"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                hx-get="{reverse('vertex-status-new')}"
            """


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_statusupdate"), name="dispatch")
class StatusUpdatesList(HorillaListView):
    """The signed-in person's own updates (ArielVertex ``status-updates/mine``)."""

    model = StatusUpdate
    filter_class = StatusUpdateFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        employee = access.employee_of(self.request.user)
        queryset = super().get_queryset()
        if employee is None:
            return queryset.none()
        return queryset.filter(employee=employee).select_related("project")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-status-list")

    @cached_property
    def columns(self):
        get_field = self.model()._meta.get_field
        return [
            (get_field("update_date").verbose_name, "update_date"),
            (_("Project"), "get_project_code"),
            (_("Status"), "get_status_html"),
            (get_field("hours_spent").verbose_name, "hours_spent"),
            (get_field("work_completed").verbose_name, "work_completed"),
            (get_field("blockers").verbose_name, "blockers"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [(get_field("update_date").verbose_name, "update_date")]

    row_status_indications = _status_indications()
    row_status_class = "status-{status}"


@method_decorator(login_required, name="dispatch")
class StatusUpdateFormView(HorillaFormView):
    model = StatusUpdate
    form_class = StatusUpdateForm
    new_display_title = _("New status update")

    def dispatch(self, request, *args, **kwargs):
        if (
            not request.user.has_perm("vertex.add_statusupdate")
            or access.employee_of(request.user) is None
        ):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        project = self.request.GET.get("project")
        if project and not form.is_bound and not form.initial.get("project"):
            form.initial["project"] = project
        return form

    def form_valid(self, form):
        update = form.save(commit=False)
        update.employee = access.employee_of(self.request.user)
        update.save()
        messages.success(self.request, _("Status update submitted."))
        if "/vertex/projects/" in self.request.META.get("HTTP_REFERER", ""):
            return self.HttpResponse(RELOAD_PAGE)
        return self.HttpResponse()


# ---------------------------------------------------------------------------
# Hiring requests page: list of resource requests, modal to raise a new one
# ---------------------------------------------------------------------------


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_resourcerequest"), name="dispatch")
class ResourceRequestsView(TemplateView):
    template_name = "vertex/cbv/hiring.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_resourcerequest"), name="dispatch")
class ResourceRequestsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = ResourceRequestFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["project", "status", "priority"]
    filter_body_template = "vertex/cbv/resource_filter.html"
    hide_actions_button = True
    create_label = _("Raise request")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-hiring-list")
        if self.request.user.has_perm("vertex.add_resourcerequest"):
            self.create_attrs = f"""
                onclick = "event.stopPropagation();"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                hx-get="{reverse('vertex-hiring-new')}"
            """


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_resourcerequest"), name="dispatch")
class ResourceRequestsList(HorillaListView):
    model = ResourceRequest
    filter_class = ResourceRequestFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(
            pk__in=access.visible_resource_requests(self.request.user).values("pk")
        ).select_related("project", "requested_by")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-hiring-list")

    @cached_property
    def columns(self):
        get_field = self.model()._meta.get_field
        return [
            (get_field("role_title").verbose_name, "role_title"),
            (_("Project"), "get_project_code"),
            (get_field("count").verbose_name, "count"),
            (get_field("priority").verbose_name, "get_priority_display"),
            (_("Status"), "get_status_html"),
            (_("Requested by"), "get_requested_by_name"),
            (get_field("expected_start_date").verbose_name, "expected_start_date"),
            (_("Raised on"), "created_at"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [
            (get_field("role_title").verbose_name, "role_title"),
            (get_field("priority").verbose_name, "priority"),
            (_("Raised on"), "created_at"),
        ]

    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """


@method_decorator(login_required, name="dispatch")
class ResourceRequestFormView(HorillaFormView):
    model = ResourceRequest
    form_class = ResourceRequestForm
    new_display_title = _("Raise resource request")

    def dispatch(self, request, *args, **kwargs):
        if (
            not request.user.has_perm("vertex.add_resourcerequest")
            or access.employee_of(request.user) is None
        ):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        project = self.request.GET.get("project")
        if project and not form.is_bound and not form.initial.get("project"):
            form.initial["project"] = project
        return form

    def form_valid(self, form):
        resource_request = form.save(commit=False)
        me = access.employee_of(self.request.user)
        resource_request.requested_by = me
        resource_request.save()
        notify_employees(
            me,
            employees_with_perm("vertex.change_resourcerequest"),
            str(_("New resource request: %(role)s (x%(count)d)."))
            % {"role": resource_request.role_title, "count": resource_request.count},
            resource_request.get_absolute_url(),
        )
        messages.success(self.request, _("Resource request raised."))
        if "/vertex/projects/" in self.request.META.get("HTTP_REFERER", ""):
            return self.HttpResponse(RELOAD_PAGE)
        return self.HttpResponse()
