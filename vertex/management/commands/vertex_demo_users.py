"""
Create (or remove) local test users for Vertex: one user per example role, each in its own
Groups and Permissions group, plus a small demo project they belong to.

    python manage.py vertex_demo_users            create / refresh
    python manage.py vertex_demo_users --remove   delete them again (and the demo project)

Accounts, groups and the password are defined in vertex/roles.py. Local testing only.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from employee.models import Employee
from vertex.models import Project, ProjectMember
from vertex.roles import DEMO_PASSWORD, DEMO_PROJECT_CODE, DEMO_USERS, ROLES, build_groups


class Command(BaseCommand):
    help = "Create or remove the Vertex demo users, groups and demo project."

    def add_arguments(self, parser):
        parser.add_argument("--remove", action="store_true", help="Delete the demo users, groups and project.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["remove"]:
            return self.remove()
        groups = build_groups()
        project, _ = Project.objects.get_or_create(
            code=DEMO_PROJECT_CODE,
            defaults={"name": "Vertex Demo Project", "start_date": date.today(), "client_name": "Demo Client"},
        )
        for username, first, last, group_name, role in DEMO_USERS:
            employee = Employee.objects.filter(employee_user_id__username=username).first()
            if employee is None:
                employee = Employee.objects.create(
                    employee_first_name=first, employee_last_name=last,
                    email=f"{username}@vertex-demo.example", phone=f"98{abs(hash(username)) % 10**8:08d}",
                )
                user = employee.employee_user_id
                user.username = username
            else:
                user = employee.employee_user_id
            user.is_new_employee = False
            user.set_password(DEMO_PASSWORD)
            user.groups.clear()
            if group_name:
                user.groups.add(groups[group_name])
            user.save()
            if role:
                ProjectMember.objects.update_or_create(
                    project=project, employee=employee,
                    defaults={"role_on_project": role, "is_active": True, "start_date": date.today(),
                              "allocation_pct": 50},
                )
            self.stdout.write(f"  {username:10} {group_name or '(no group)'}")
        self.stdout.write(self.style.SUCCESS(f"{len(DEMO_USERS)} demo users ready; project {DEMO_PROJECT_CODE}."))

    def remove(self):
        User = get_user_model()
        usernames = [u[0] for u in DEMO_USERS]
        Project.objects.filter(code=DEMO_PROJECT_CODE).delete()
        for employee in Employee.objects.filter(employee_user_id__username__in=usernames):
            # a payroll contract is created with every employee and must go first
            try:
                from payroll.models.models import Contract

                Contract.objects.filter(employee_id=employee).delete()
            except Exception:  # pragma: no cover - the payroll app layout may differ
                pass
            employee.delete()
        User.objects.filter(username__in=usernames).delete()
        from django.contrib.auth.models import Group

        Group.objects.filter(name__in=list(ROLES)).delete()
        self.stdout.write(self.style.SUCCESS("Demo users, groups and project removed."))
