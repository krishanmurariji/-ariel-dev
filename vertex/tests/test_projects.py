"""
vertex/tests/test_projects.py

Vertex Projects follow ordinary Ariel HR Groups and Permissions:
* admin (superuser) needs no permission and no group
* a user with no group sees nothing
* a group's permissions decide what its users can do
* project data scoping (member-only vs see-all) is ported from ArielVertex.
"""

import shutil
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employee.models import Employee
from horilla.horilla_middlewares import _thread_locals, set_selected_company
from horilla.media_access import may_open
from vertex import access
from vertex.models import (
    Project,
    ProjectComment,
    ProjectDocument,
    ProjectMember,
    ProjectRole,
    Visibility,
)

PASSWORD = "123qwe"
HX = {"HTTP_HX_REQUEST": "true"}  # modal forms are loaded by HTMX
User = get_user_model()


def make_employee(first_name, email, phone):
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    user = employee.employee_user_id
    user.is_new_employee = False  # else a middleware forces /change-password/
    user.set_password(PASSWORD)
    user.save()
    return Employee.objects.get(pk=employee.pk)


def grant(employee, *codenames):
    """Put the employee in a group holding the given vertex permissions."""
    group = Group.objects.create(name=f"G-{employee.pk}-{'-'.join(codenames)}"[:150])
    group.permissions.set(
        Permission.objects.filter(content_type__app_label="vertex", codename__in=codenames)
    )
    employee.employee_user_id.groups.add(group)


def make_project(code, **extra):
    return Project.objects.create(
        code=code, name=f"Project {code}", start_date=date(2026, 1, 1), **extra
    )


def join(project, employee, role=ProjectRole.DEVELOPER):
    return ProjectMember.objects.create(
        project=project, employee=employee, role_on_project=role, start_date=date(2026, 1, 1)
    )


class VertexTestBase(TestCase):
    def setUp(self):
        # a previous test's request leaks its user/company through context variables
        _thread_locals.request = None
        set_selected_company(None)

    def login(self, employee):
        client = Client()
        self.assertTrue(
            client.login(username=employee.employee_user_id.username, password=PASSWORD)
        )
        return client


class PermissionGridTests(TestCase):
    def test_vertex_permissions_exist_for_the_grid(self):
        codenames = set(
            Permission.objects.filter(content_type__app_label="vertex").values_list(
                "codename", flat=True
            )
        )
        for expected in (
            "view_project", "add_project", "change_project", "delete_project",
            "view_allprojects", "change_allprojects",
            "add_projectmember", "delete_projectmember",
            "add_projectdocument", "delete_projectdocument", "add_projectcall",
        ):
            self.assertIn(expected, codenames)

    def test_all_projects_is_its_own_row_in_the_permission_grid(self):
        from base.views import get_models_in_app

        names = {m._meta.model_name for m in get_models_in_app("vertex")}
        self.assertIn("allprojects", names)
        self.assertIn("project", names)


class NoPermissionTests(VertexTestBase):
    def test_user_without_a_group_sees_nothing(self):
        worker = make_employee("Worker", "w@example.com", "9000000001")
        project = make_project("AAA")
        join(project, worker)
        client = self.login(worker)
        self.assertNotEqual(client.get(reverse("vertex-projects")).status_code, 200)
        self.assertNotEqual(client.get(project.get_absolute_url()).status_code, 200)
        self.assertEqual(access.visible_projects(worker.employee_user_id).count(), 0)

    def test_admin_needs_no_group_and_no_permission(self):
        admin = make_employee("Admin", "a@example.com", "9000000002")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        project = make_project("BBB")
        client = self.login(admin)
        self.assertEqual(client.get(reverse("vertex-projects")).status_code, 200)
        self.assertEqual(client.get(reverse("vertex-project-new"), **HX).status_code, 200)
        for tab in ("overview", "team", "documents", "calls", "comments"):
            response = client.get(project.get_absolute_url(), {"tab": tab})
            self.assertEqual(response.status_code, 200, tab)
        self.assertEqual(
            client.get(reverse("vertex-project-edit", args=[project.pk]), **HX).status_code, 200
        )


class DataScopingTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.me = make_employee("Me", "me@example.com", "9000000011")
        self.mine = make_project("MINE")
        self.other = make_project("OTHER")
        join(self.mine, self.me)
        grant(self.me, "view_project")

    def test_member_sees_only_own_projects(self):
        client = self.login(self.me)
        self.assertEqual(client.get(reverse("vertex-projects")).status_code, 200)
        response = client.get(reverse("vertex-project-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MINE")
        self.assertNotContains(response, "OTHER")
        card = client.get(reverse("vertex-project-card"), **HX)
        self.assertContains(card, "MINE")
        self.assertNotContains(card, "OTHER")

    def test_a_project_of_someone_else_is_a_404(self):
        client = self.login(self.me)
        self.assertEqual(client.get(self.other.get_absolute_url()).status_code, 404)

    def test_inactive_membership_gives_no_access(self):
        ProjectMember.objects.filter(project=self.mine, employee=self.me).update(is_active=False)
        client = self.login(self.me)
        self.assertEqual(client.get(self.mine.get_absolute_url()).status_code, 404)

    def test_view_allprojects_shows_everything(self):
        grant(self.me, "view_allprojects")
        client = self.login(self.me)
        response = client.get(reverse("vertex-project-list"))
        self.assertContains(response, "MINE")
        self.assertContains(response, "OTHER")

    def test_search_and_status_filter(self):
        grant(self.me, "view_allprojects")
        Project.objects.filter(pk=self.other.pk).update(status="completed")
        client = self.login(self.me)
        response = client.get(reverse("vertex-project-list"), {"status": "completed"})
        self.assertContains(response, "OTHER")
        self.assertNotContains(response, "MINE")
        response = client.get(reverse("vertex-project-list"), {"search": "mine"})
        self.assertContains(response, "MINE")
        self.assertNotContains(response, "OTHER")


class CreateAndManageTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@example.com", "9000000021")
        self.dev = make_employee("Dev", "dev@example.com", "9000000022")
        self.project = make_project("DELIV")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev, ProjectRole.DEVELOPER)

    def assertNoForm(self, response):
        """Denied HTMX requests get Ariel HR's standard no-permission box instead of the form."""
        self.assertNotContains(response, 'name="client_name"')

    def new_project_data(self, code="NEW1"):
        return {
            "code": code, "name": "Brand new", "status": "active", "health": "green",
            "priority": 1, "start_date": "2026-02-01",
        }

    def test_description_and_notes_are_capped(self):
        grant(self.pm, "view_project", "add_project")
        client = self.login(self.pm)
        page = client.get(reverse("vertex-project-new"), **HX)
        self.assertContains(page, 'maxlength="1000"')
        self.assertContains(page, 'maxlength="2000"')
        client.post(reverse("vertex-project-new"), {**self.new_project_data("CAP1"), "description": "x" * 1001}, **HX)
        client.post(reverse("vertex-project-new"), {**self.new_project_data("CAP2"), "notes": "x" * 2001}, **HX)
        self.assertFalse(Project.objects.filter(code__in=["CAP1", "CAP2"]).exists())
        client.post(reverse("vertex-project-new"), {**self.new_project_data("CAP3"), "description": "x" * 1000, "notes": "y" * 2000}, **HX)
        self.assertTrue(Project.objects.filter(code="CAP3").exists())

    def test_create_needs_add_project_and_makes_creator_the_pm(self):
        grant(self.dev, "view_project")
        response = self.login(self.dev).post(
            reverse("vertex-project-new"), self.new_project_data(), **HX
        )
        self.assertFalse(Project.objects.filter(code="NEW1").exists())

        grant(self.dev, "add_project")
        self.login(self.dev).post(reverse("vertex-project-new"), self.new_project_data(), **HX)
        created = Project.objects.get(code="NEW1")
        member = ProjectMember.objects.get(project=created, employee=self.dev)
        self.assertEqual(member.role_on_project, ProjectRole.PROJECT_MANAGER)

    def test_duplicate_code_is_refused(self):
        grant(self.pm, "view_project", "add_project")
        self.login(self.pm).post(
            reverse("vertex-project-new"), self.new_project_data(code="deliv"), **HX
        )
        self.assertEqual(Project.objects.filter(code="DELIV").count(), 1)

    def test_only_pm_or_pc_with_change_permission_can_edit(self):
        grant(self.pm, "view_project", "change_project")
        grant(self.dev, "view_project", "change_project")
        edit = reverse("vertex-project-edit", args=[self.project.pk])
        self.assertContains(self.login(self.pm).get(edit, **HX), 'name="client_name"')
        self.assertNoForm(self.login(self.dev).get(edit, **HX))

    def test_pm_without_change_permission_cannot_edit(self):
        grant(self.pm, "view_project")
        edit = reverse("vertex-project-edit", args=[self.project.pk])
        self.assertNoForm(self.login(self.pm).get(edit, **HX))

    def test_change_allprojects_edits_any_project(self):
        grant(self.dev, "view_allprojects", "change_allprojects", "view_project")
        edit = reverse("vertex-project-edit", args=[self.project.pk])
        self.assertContains(self.login(self.dev).get(edit, **HX), 'name="client_name"')

    def test_project_code_cannot_be_changed_on_edit(self):
        grant(self.pm, "view_project", "change_project")
        data = self.new_project_data(code="HACKED")
        data["name"] = "Renamed"
        self.login(self.pm).post(
            reverse("vertex-project-edit", args=[self.project.pk]), data, **HX
        )
        self.project.refresh_from_db()
        self.assertEqual((self.project.code, self.project.name), ("DELIV", "Renamed"))

    def test_add_and_remove_member_keeps_history(self):
        newcomer = make_employee("New", "new@example.com", "9000000023")
        grant(self.pm, "view_project", "change_project", "add_projectmember", "delete_projectmember")
        client = self.login(self.pm)
        data = {
            "employee": newcomer.pk, "role_on_project": "qa", "allocation_pct": 50,
            "start_date": "2026-03-01",
        }
        client.post(reverse("vertex-member-add", args=[self.project.pk]), data, **HX)
        member = ProjectMember.objects.get(project=self.project, employee=newcomer)
        self.assertTrue(member.is_active)
        # adding twice is refused
        client.post(reverse("vertex-member-add", args=[self.project.pk]), data, **HX)
        self.assertEqual(ProjectMember.objects.filter(employee=newcomer).count(), 1)
        client.post(reverse("vertex-member-remove", args=[self.project.pk, member.pk]))
        member.refresh_from_db()
        self.assertFalse(member.is_active)
        # re-adding reactivates the same row
        client.post(reverse("vertex-member-add", args=[self.project.pk]), data, **HX)
        member.refresh_from_db()
        self.assertTrue(member.is_active)
        self.assertEqual(ProjectMember.objects.filter(employee=newcomer).count(), 1)

    def test_non_manager_cannot_add_member(self):
        newcomer = make_employee("New", "new@example.com", "9000000023")
        grant(self.dev, "view_project", "add_projectmember")
        self.login(self.dev).post(
            reverse("vertex-member-add", args=[self.project.pk]),
            {"employee": newcomer.pk, "role_on_project": "qa", "allocation_pct": 50,
             "start_date": "2026-03-01"},
            **HX,
        )
        self.assertFalse(ProjectMember.objects.filter(employee=newcomer).exists())

    def test_allocation_over_100_is_refused(self):
        newcomer = make_employee("New", "new@example.com", "9000000023")
        grant(self.pm, "view_project", "change_project", "add_projectmember")
        self.login(self.pm).post(
            reverse("vertex-member-add", args=[self.project.pk]),
            {"employee": newcomer.pk, "role_on_project": "qa", "allocation_pct": 150,
             "start_date": "2026-03-01"},
            **HX,
        )
        self.assertFalse(ProjectMember.objects.filter(employee=newcomer).exists())

    def test_writes_by_get_are_refused(self):
        grant(
            self.pm, "view_project", "change_project", "add_projectmember",
            "delete_projectmember", "add_projectcall", "add_projectdocument",
        )
        member = ProjectMember.objects.get(project=self.project, employee=self.dev)
        client = self.login(self.pm)
        for name, args in (
            ("vertex-member-add", [self.project.pk]),
            ("vertex-member-remove", [self.project.pk, member.pk]),
            ("vertex-comment-add", [self.project.pk]),
            ("vertex-call-add", [self.project.pk]),
            ("vertex-document-add", [self.project.pk]),
        ):
            self.assertEqual(client.get(reverse(name, args=args)).status_code, 405, name)
        member.refresh_from_db()
        self.assertTrue(member.is_active)


class CommentsAndCallsTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@example.com", "9000000031")
        self.dev = make_employee("Dev", "dev@example.com", "9000000032")
        self.outsider = make_employee("Out", "out@example.com", "9000000033")
        self.project = make_project("TALK")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev)
        for person in (self.pm, self.dev, self.outsider):
            grant(person, "view_project")

    def test_any_member_can_comment_but_not_an_outsider(self):
        url = reverse("vertex-comment-add", args=[self.project.pk])
        self.login(self.dev).post(url, {"message": "hello team"})
        self.assertEqual(ProjectComment.objects.filter(project=self.project).count(), 1)
        self.assertEqual(self.login(self.outsider).post(url, {"message": "hi"}).status_code, 404)
        self.assertEqual(ProjectComment.objects.filter(project=self.project).count(), 1)

    def test_blank_comment_is_refused(self):
        self.login(self.dev).post(
            reverse("vertex-comment-add", args=[self.project.pk]), {"message": "   "}
        )
        self.assertFalse(ProjectComment.objects.exists())

    def test_scheduling_a_call_needs_permission_and_manage_rights(self):
        data = {
            "title": "Kickoff", "call_type": "customer", "scheduled_at": "2026-03-01T10:00",
            "duration_minutes": 30,
        }
        url = reverse("vertex-call-add", args=[self.project.pk])
        grant(self.dev, "add_projectcall")
        self.login(self.dev).post(url, data, **HX)  # a developer is not PM/PC
        self.assertFalse(self.project.calls.exists())
        grant(self.pm, "add_projectcall", "change_project")
        self.login(self.pm).post(url, data, **HX)
        self.assertEqual(self.project.calls.count(), 1)


MEDIA_DIR = tempfile.mkdtemp(prefix="vertex-media-")


@override_settings(MEDIA_ROOT=MEDIA_DIR)
class DocumentTests(VertexTestBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_DIR, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@example.com", "9000000041")
        self.biz = make_employee("Biz", "biz@example.com", "9000000042")
        self.outsider = make_employee("Out", "out@example.com", "9000000043")
        self.project = make_project("DOCS")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.biz, ProjectRole.BUSINESS_PERSON)
        for person in (self.pm, self.biz, self.outsider):
            grant(person, "view_project")
        grant(self.pm, "add_projectdocument", "delete_projectdocument", "change_project")
        self.internal = ProjectDocument.objects.create(
            project=self.project, title="Internal plan", visibility=Visibility.INTERNAL,
            file=SimpleUploadedFile("plan.txt", b"secret"), file_name="plan.txt", size_bytes=6,
        )
        self.shared = ProjectDocument.objects.create(
            project=self.project, title="Shared spec", visibility=Visibility.BUSINESS_VISIBLE,
            file=SimpleUploadedFile("spec.txt", b"public"), file_name="spec.txt", size_bytes=6,
        )

    def download(self, person, document):
        response = self.login(person).get(
            reverse("vertex-document-download", args=[self.project.pk, document.pk])
        )
        if response.streaming:
            b"".join(response.streaming_content)  # reads and closes the file
        return response

    def test_member_downloads_as_an_attachment(self):
        response = self.download(self.pm, self.internal)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_business_person_never_sees_internal_documents(self):
        page = self.login(self.biz).get(self.project.get_absolute_url(), {"tab": "documents"})
        self.assertContains(page, "Shared spec")
        self.assertNotContains(page, "Internal plan")
        self.assertEqual(self.download(self.biz, self.internal).status_code, 404)
        self.assertEqual(self.download(self.biz, self.shared).status_code, 200)

    def test_outsider_cannot_download(self):
        self.assertEqual(self.download(self.outsider, self.shared).status_code, 404)

    def test_media_url_follows_the_same_rule(self):
        self.assertTrue(may_open(self.pm.employee_user_id, self.internal.file.name))
        self.assertFalse(may_open(self.biz.employee_user_id, self.internal.file.name))
        self.assertTrue(may_open(self.biz.employee_user_id, self.shared.file.name))
        self.assertFalse(may_open(self.outsider.employee_user_id, self.shared.file.name))

    def test_upload_blocks_risky_file_types(self):
        url = reverse("vertex-document-add", args=[self.project.pk])
        before = ProjectDocument.objects.count()
        self.login(self.pm).post(
            url,
            {"title": "Bad", "category": "other", "visibility": 0,
             "file": SimpleUploadedFile("page.html", b"<script>1</script>")},
            **HX,
        )
        self.assertEqual(ProjectDocument.objects.count(), before)

    def test_upload_and_delete(self):
        url = reverse("vertex-document-add", args=[self.project.pk])
        self.login(self.pm).post(
            url,
            {"title": "Notes", "category": "meeting_notes", "visibility": 1,
             "file": SimpleUploadedFile("notes.txt", b"abc")},
            **HX,
        )
        document = ProjectDocument.objects.get(title="Notes")
        self.assertEqual((document.file_name, document.size_bytes), ("notes.txt", 3))
        self.assertEqual(document.uploaded_by, self.pm)
        self.login(self.pm).post(
            reverse("vertex-document-delete", args=[self.project.pk, document.pk])
        )
        self.assertFalse(ProjectDocument.objects.filter(title="Notes").exists())

    def test_document_description_is_capped(self):
        url = reverse("vertex-document-add", args=[self.project.pk])
        page = self.login(self.pm).get(url, **HX)
        self.assertContains(page, 'maxlength="500"')
        base = {"title": "Demo", "category": "demo", "visibility": 0, "is_video_link": "on",
                "video_url": "https://example.com/v"}
        self.login(self.pm).post(url, {**base, "description": "x" * 501}, **HX)
        self.assertFalse(ProjectDocument.objects.filter(title="Demo").exists())
        self.login(self.pm).post(url, {**base, "description": "x" * 500}, **HX)
        self.assertTrue(ProjectDocument.objects.filter(title="Demo").exists())

    def test_video_link_needs_a_url(self):
        url = reverse("vertex-document-add", args=[self.project.pk])
        before = ProjectDocument.objects.count()
        self.login(self.pm).post(
            url, {"title": "Demo", "category": "demo", "visibility": 0, "is_video_link": "on"}, **HX
        )
        self.assertEqual(ProjectDocument.objects.count(), before)
        self.login(self.pm).post(
            url,
            {"title": "Demo", "category": "demo", "visibility": 0, "is_video_link": "on",
             "video_url": "https://example.com/v"},
            **HX,
        )
        self.assertEqual(ProjectDocument.objects.count(), before + 1)


class WorkspaceTests(VertexTestBase):
    """The workspace tabs render for the right people, and the modals open only for managers."""

    TABS = ("overview", "team", "documents", "calls", "status", "reviews", "comments")

    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@example.com", "9000000061")
        self.dev = make_employee("Dev", "dev@example.com", "9000000062")
        self.project = make_project("WORK", client_name="Acme", tags="a, b")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev)
        for person in (self.pm, self.dev):
            grant(person, "view_project", "view_statusupdate", "view_reviewrequest")
        grant(self.pm, "change_project", "add_projectmember", "add_projectdocument", "add_projectcall")

    def test_every_tab_renders(self):
        client = self.login(self.pm)
        for tab in self.TABS:
            response = client.get(self.project.get_absolute_url(), {"tab": tab})
            self.assertEqual(response.status_code, 200, tab)
        self.assertContains(client.get(self.project.get_absolute_url()), "Acme")

    def test_unknown_tab_falls_back_to_overview(self):
        response = self.login(self.dev).get(self.project.get_absolute_url(), {"tab": "nope"})
        self.assertEqual(response.status_code, 200)

    def test_buttons_follow_permissions(self):
        manager = self.login(self.pm).get(self.project.get_absolute_url(), {"tab": "team"})
        self.assertContains(manager, reverse("vertex-member-add", args=[self.project.pk]))
        self.assertContains(manager, reverse("vertex-project-edit", args=[self.project.pk]))
        member = self.login(self.dev).get(self.project.get_absolute_url(), {"tab": "team"})
        self.assertNotContains(member, reverse("vertex-member-add", args=[self.project.pk]))
        self.assertNotContains(member, reverse("vertex-project-edit", args=[self.project.pk]))

    def test_modals_open_for_a_manager_and_not_for_a_member(self):
        for name in ("vertex-member-add", "vertex-document-add", "vertex-call-add"):
            url = reverse(name, args=[self.project.pk])
            self.assertContains(self.login(self.pm).get(url, **HX), "<form")
            denied = self.login(self.dev).get(url, **HX)
            self.assertNotContains(denied, "<form")

    def test_document_modal_does_not_need_manage_rights(self):
        grant(self.dev, "add_projectdocument")
        url = reverse("vertex-document-add", args=[self.project.pk])
        self.assertContains(self.login(self.dev).get(url, **HX), "<form")

    def test_modal_of_a_project_the_user_cannot_see_is_refused(self):
        other = make_project("HIDDEN")
        grant(self.pm, "add_projectmember")
        url = reverse("vertex-member-add", args=[other.pk])
        self.assertNotContains(self.login(self.pm).get(url, **HX), "<form")

    def test_start_date_after_end_date_is_refused(self):
        grant(self.pm, "add_project")
        self.login(self.pm).post(
            reverse("vertex-project-new"),
            {"code": "BADD", "name": "x", "status": "active", "health": "green",
             "priority": 1, "start_date": "2026-05-01", "expected_end_date": "2026-04-01"},
            **HX,
        )
        self.assertFalse(Project.objects.filter(code="BADD").exists())

    def test_card_view_and_filters_work(self):
        client = self.login(self.pm)
        card = client.get(reverse("vertex-project-card"), **HX)
        self.assertContains(card, "WORK")
        self.assertEqual(client.get(reverse("vertex-project-nav"), **HX).status_code, 200)
        listing = client.get(reverse("vertex-project-list"), {"health": "red"}, **HX)
        self.assertNotContains(listing, "WORK")
