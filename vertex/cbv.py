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
from vertex import access, meetings
from vertex.filters import (
    MeetingMinuteFilter,
    ResourceRequestFilter,
    ReviewRequestFilter,
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
from vertex.forms_reviews import MinutesForm, ReviewRequestForm
from vertex.models import (
    MeetingMinute,
    MinuteStatus,
    Project,
    ProjectCall,
    ProjectDocument,
    ProjectMember,
    ProjectRole,
    ResourceRequest,
    ReviewMeeting,
    ReviewRequest,
    ReviewRequestStatus,
    StatusUpdate,
)
from vertex.notify import employees_with_perm, notify_employees


def vertex_structured(self):
    """Render a Vertex modal form with vertex/modal_form.html (see VertexModal)."""
    from django.template.loader import render_to_string

    from horilla.horilla_middlewares import _thread_locals

    request = getattr(_thread_locals, "request", None)
    # request= runs the context processors, so the modal picks up the company theme colours
    return render_to_string("vertex/modal_form.html", {"form": self}, request=request)


class VertexResponse:
    """
    Reply of a Vertex modal after a successful save: close the modal and refresh the list.

    Ariel HR's stock reply also clicks "reload messages", and so does the list when it reloads, so a
    toast was fetched twice at the same moment and shown twice. The list (or the page reload) already
    shows the toast, so this reply never asks for it a second time.
    """

    def __new__(cls, content="", targets_to_reload=None, script=""):
        import json

        from django.http import HttpResponse as DjangoResponse

        from horilla.horilla_middlewares import _thread_locals
        from horilla_views.cbv_methods import get_short_uuid

        request = getattr(_thread_locals, "request", None)
        script_id = get_short_uuid(4)
        targets = [t for t in set(targets_to_reload or []) if t != "#reloadMessagesButton"]
        clicks = "".join(f"$(`{t}`).click();" for t in targets)
        again = bool(request and request.method == "POST" and request.POST.get("save_and_add_another") == "true")
        if again:
            reopen = request.path + (f"?{request.META['QUERY_STRING']}" if request.META.get("QUERY_STRING") else "")
            target = request.META.get("HTTP_HX_TARGET", "genericModalBody")
            script += (
                f"setTimeout(function(){{if(window.htmx){{htmx.ajax('GET',{json.dumps(reopen)},"
                f"{{target:{json.dumps('#' + target)},swap:'innerHTML'}});}}}},50);"
            )
            close = ""
        else:
            close = f"$('#scriptTarget{script_id}').closest('.oh-modal--show').first().removeClass('oh-modal--show');"
        body = (
            f"<script id='scriptTarget{script_id}'>{clicks}{close}"
            f"$('.reload-record').click();$('.reload-field').click();{script}</script>"
        )
        return DjangoResponse(body + (content or ""))


class VertexModal:
    """Mixin for Vertex modal form views: swaps the form body for the Vertex modal design."""

    HttpResponse = VertexResponse

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.form_class:
            self.form_class.structured = vertex_structured


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
            (_("Status"), "get_status_display"),
            (_("Health"), "get_health_html"),
            (_("Priority"), "get_priority_display"),
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
            (_("Priority"), "priority"),
            (get_field("start_date").verbose_name, "start_date"),
        ]

    row_status_indications = STATUS_INDICATIONS
    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Edit"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv.can_edit_project",
            "attrs": """
                hx-get='{get_update_url}'
                hx-target='#genericModalBody'
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
            """,
        },
    ]


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
                "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" class="oh-dropdown__link" """,
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


def can_change_minutes(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.change_meetingminute")


def can_delete_minutes(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.delete_meetingminute")


@method_decorator(login_required, name="dispatch")
class ProjectFormView(VertexModal, HorillaFormView):
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
class _ProjectChildFormView(VertexModal, HorillaFormView):
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
        # the call is saved first; a meeting provider problem never undoes it
        result = meetings.generate_call_meeting(call, self.request)
        if result.get("success"):
            messages.success(self.request, _("Call scheduled and meeting link generated."))
        else:
            meetings.report(self.request, call, result, scheduled=True)
        return self.HttpResponse(RELOAD_PAGE)


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
            (_("Blockers"), "blockers"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [(get_field("update_date").verbose_name, "update_date")]

    row_status_indications = _status_indications()
    row_status_class = "status-{status}"
    actions_menu = True
    actions = [
        {
            "action": _("Open project"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_project_url}?tab=status'" """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class StatusUpdateFormView(VertexModal, HorillaFormView):
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
            (_("Role"), "role_title"),
            (_("Project"), "get_project_code"),
            (_("Count"), "count"),
            (_("Priority"), "get_priority_display"),
            (_("Status"), "get_status_html"),
            (_("Requested by"), "get_requested_by_name"),
            (_("Expected start"), "expected_start_date"),
            (_("Raised on"), "get_raised_on"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [
            (_("Role"), "role_title"),
            (_("Priority"), "priority"),
            (_("Raised on"), "created_at"),
        ]

    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Open project"),
            "icon": "folder-open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_project_url}'" """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class ResourceRequestFormView(VertexModal, HorillaFormView):
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


# ---------------------------------------------------------------------------
# Reviews page: list of review requests, modal to request / schedule reviews
# ---------------------------------------------------------------------------


def _filter_indications(field, items):
    def js(value):
        return f"""
            onclick="
                $('#applyFilter').closest('form').find('[name={field}]').val('{value}');
                $('#applyFilter').click();
            "
        """

    return [(f"{value}--dot", label, js(value)) for value, label in items]


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_reviewrequest"), name="dispatch")
class ReviewsView(TemplateView):
    template_name = "vertex/cbv/reviews.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_reviewrequest"), name="dispatch")
class ReviewsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = ReviewRequestFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["project", "status", "review_type"]
    filter_body_template = "vertex/cbv/review_filter.html"
    hide_actions_button = True
    create_label = _("Request review")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-review-list")
        if self.request.user.has_perm("vertex.add_reviewrequest"):
            self.create_attrs = f"""
                onclick = "event.stopPropagation();"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                hx-get="{reverse('vertex-review-new')}"
            """


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_reviewrequest"), name="dispatch")
class ReviewsList(HorillaListView):
    model = ReviewRequest
    filter_class = ReviewRequestFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(
            pk__in=access.visible_reviews(self.request.user).values("pk")
        ).select_related("project", "subject", "assigned_to", "meeting")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-review-list")

    @cached_property
    def columns(self):
        get_field = self.model()._meta.get_field
        return [
            (_("Employee"), "get_subject_name"),
            (_("Project"), "get_project_code"),
            (_("Type"), "get_review_type_display"),
            (_("Reviewer"), "get_reviewer_name"),
            (_("Status"), "get_status_html"),
            (_("Meeting"), "get_meeting_time"),
            (_("Due"), "due_date"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [
            (_("Type"), "review_type"),
            (_("Due"), "due_date"),
            (_("Requested on"), "created_at"),
        ]

    row_status_indications = _filter_indications("status", ReviewRequestStatus.choices)
    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Open project"),
            "icon": "folder-open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_project_url}?tab=reviews'" """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class ReviewRequestFormView(VertexModal, HorillaFormView):
    """Modal: request (or immediately schedule) a review for one or many employees."""

    model = ReviewRequest
    form_class = ReviewRequestForm
    new_display_title = _("Request / schedule review")

    def dispatch(self, request, *args, **kwargs):
        if (
            not request.user.has_perm("vertex.add_reviewrequest")
            or access.employee_of(request.user) is None
        ):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return None  # always a new request

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        project = self.request.GET.get("project")
        if project and not form.is_bound and not form.initial.get("project"):
            form.initial["project"] = project
        return form

    def form_valid(self, form):
        from vertex.views_reviews import _attendees

        data = form.cleaned_data
        me = access.employee_of(self.request.user)
        subjects = list(data["subjects"])
        assignee = data["assigned_to"]
        scheduled_at = data["scheduled_at"]
        attendees = _attendees(subjects, assignee, me, data["extra_attendees"])
        created_meetings = []
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
                created_meetings.append(
                    ReviewMeeting.objects.create(
                        review_request=review,
                        title=data["title"],
                        scheduled_at=scheduled_at,
                        duration_minutes=data["duration_minutes"] or 30,
                        attendees=attendees,
                        scheduled_by=me,
                    )
                )
        result = None
        if created_meetings:
            # one calendar event for the whole group, shared by every review's meeting
            result = meetings.generate_review_meeting(created_meetings[0], self.request)
            meetings.share_meeting(created_meetings[0], created_meetings[1:])
        verb = (
            _("A review has been scheduled for you.")
            if scheduled_at
            else _("A review has been requested for you.")
        )
        notify_employees(me, subjects, str(verb), reverse("vertex-reviews"))
        if assignee:
            notify_employees(
                me, [assignee], str(_("You have been asked to run a review.")), reverse("vertex-reviews")
            )
        if result is not None and result.get("success"):
            messages.success(
                self.request, _("%(n)d review(s) created and the meeting link generated.") % {"n": len(subjects)}
            )
        else:
            messages.success(self.request, _("%(n)d review(s) created.") % {"n": len(subjects)})
            if result is not None:
                meetings.report(self.request, created_meetings[0], result, scheduled=True, what="review")
        return self.HttpResponse()


# ---------------------------------------------------------------------------
# Meeting minutes page: only the minutes the signed-in person captured
# ---------------------------------------------------------------------------


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_meetingminute"), name="dispatch")
class MinutesView(TemplateView):
    template_name = "vertex/cbv/minutes.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_meetingminute"), name="dispatch")
class MinutesNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = MeetingMinuteFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status"]
    filter_body_template = "vertex/cbv/minutes_filter.html"
    hide_actions_button = True
    create_label = _("New minutes")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-minutes-list")
        if self.request.user.has_perm("vertex.add_meetingminute"):
            self.create_attrs = f"""
                onclick = "event.stopPropagation();"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                hx-get="{reverse('vertex-minute-new')}"
            """


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_meetingminute"), name="dispatch")
class MinutesList(HorillaListView):
    model = MeetingMinute
    filter_class = MeetingMinuteFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        employee = access.employee_of(self.request.user)
        if employee is None:
            return queryset.none()
        return queryset.filter(created_by=employee)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-minutes-list")

    @cached_property
    def columns(self):
        get_field = self.model()._meta.get_field
        return [
            (_("Title"), "title"),
            (_("Meeting date"), "get_meeting_when"),
            (_("Location"), "location"),
            (_("Status"), "get_status_html"),
            (_("Sent"), "get_sent_display"),
        ]

    @cached_property
    def sortby_mapping(self):
        get_field = self.model()._meta.get_field
        return [
            (_("Title"), "title"),
            (_("Meeting date"), "meeting_date"),
        ]

    row_status_indications = _filter_indications("status", MinuteStatus.choices)
    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Edit"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv.can_change_minutes",
            "attrs": """
                hx-get='{get_update_url}'
                hx-target='#genericModalBody'
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
            """,
        },
        {
            "action": _("Delete"),
            "icon": "trash-outline",
            "accessibility": "vertex.cbv.can_delete_minutes",
            "attrs": """
                hx-post='{get_delete_url}'
                hx-confirm='Delete these minutes? This cannot be undone.'
                hx-swap='none'
                hx-on::after-request='window.location.reload()'
            """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class MinutesFormView(VertexModal, HorillaFormView):
    """Modal: capture new minutes, or correct ones the signed-in person captured."""

    model = MeetingMinute
    form_class = MinutesForm
    new_display_title = _("New meeting minutes")

    def dispatch(self, request, *args, **kwargs):
        employee = access.employee_of(request.user)
        pk = kwargs.get("pk")
        if employee is None:
            return handle_no_permission(request)
        if pk:
            allowed = (
                request.user.has_perm("vertex.change_meetingminute")
                and MeetingMinute.objects.filter(pk=pk, created_by=employee).exists()
            )
        else:
            allowed = request.user.has_perm("vertex.add_meetingminute")
        if not allowed:
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Edit meeting minutes")
        return context

    def form_valid(self, form):
        from vertex.minutes import split_emails

        creating = form.instance.pk is None
        minute = form.save(commit=False)
        if creating:
            minute.created_by = access.employee_of(self.request.user)
        minute.attendees = ", ".join(split_emails(minute.attendees))
        # editing approved or sent minutes sends them back to Preview to be approved again
        if (
            not creating
            and minute.status in (MinuteStatus.APPROVED, MinuteStatus.SENT)
            and minute.minutes_text.strip()
        ):
            minute.status = MinuteStatus.PREVIEW
            minute.approved_by = None
            minute.approved_at = None
        minute.save()
        if creating:
            messages.success(
                self.request, _("Notes saved. Generate the minutes to preview them.")
            )
            # straight to the minutes page, where they are generated
            return self.HttpResponse(
                f"<script>window.location.href='{minute.get_absolute_url()}'</script>"
            )
        messages.success(self.request, _("Changes saved."))
        return self.HttpResponse(RELOAD_PAGE)
