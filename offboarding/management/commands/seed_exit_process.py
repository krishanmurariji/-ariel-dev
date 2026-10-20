"""
python manage.py seed_exit_process            # fill the Exit Process with sample data
python manage.py seed_exit_process --clear    # remove everything this command created

Sample data for trying out the Exit Process screens on a LOCAL PostgreSQL database:
people leaving at every stage (requests waiting for review, rejected / retained /
withdrawn, exits in progress with half-done checklists, exited, cancelled),
notice period policies, exit task groups and exit survey questions.

It goes through the same service functions the screens use (offboarding.exit_service,
exit_tasks, exit_survey), so the activity log, tasks and surveys are consistent.

Safety: it refuses to run unless the database is PostgreSQL on this computer. The
people it creates all have an e-mail ending in DEMO_DOMAIN, which is how --clear
finds them; existing employees are never changed.
"""

import datetime
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from base.models import (
    Company,
    Department,
    EmployeeType,
    JobPosition,
    JobRole,
)
from employee.models import Employee, EmployeeWorkInformation
from horilla.horilla_middlewares import _thread_locals
from horilla_auth.models import HorillaUser
from offboarding import exit_service, exit_survey, exit_tasks
from offboarding.models import (
    EmployeeNoticePolicy,
    ExitActivity,
    ExitReasonOption,
    ExitSurvey,
    ExitSurveyQuestion,
    ExitTask,
    ExitTaskConfig,
    ExitTaskGroupRule,
    NoticePeriodPolicy,
    OffboardingEmployee,
    ResignationLetter,
)
from onboarding.models import TaskTemplate, TaskTemplateTask
from payroll.models.models import Contract

DEMO_DOMAIN = "@demo-exit.example.com"
DEMO_PASSWORD = "DemoExit@123"  # local sample logins only

POLICIES = [
    ("Standard notice", "Applies to most employees.", 30, True),
    ("Senior staff notice", "Team leads and above.", 60, False),
    ("Probation notice", "For people still on probation.", 15, False),
]

SURVEY_QUESTIONS = [
    ("What is the main reason you are leaving?", "options",
     "Better pay, Career growth, Work-life balance, Relocation, Higher studies, Other", True),
    ("How would you rate your overall experience here?", "rating", "", True),
    ("How would you rate your relationship with your reporting manager?", "rating", "", True),
    ("Which of these did you value most?", "multiple",
     "Team culture, Learning, Flexibility, Leadership, Compensation, Projects", False),
    ("Would you recommend us to a friend as a place to work?", "checkbox", "", True),
    ("Would you consider rejoining in the future?", "checkbox", "", False),
    ("What could we have done better?", "textarea", "", False),
    ("Anything you would like to say to the team?", "text", "", False),
]

# (title, description, owner, approver, trigger offset from last working day, due after days)
#  owner / approver: "employee", "manager", "hr", "admin" or None
TASK_GROUPS = [
    {
        "title": "IT and Access Clearance",
        "exit_type": "all",
        "departments": [],
        "tasks": [
            ("Collect laptop and accessories", "Laptop, charger, mouse and any other issued items.", "admin", None, -3, 3),
            ("Revoke system access", "Disable email, VPN, code repositories and internal tools.", "admin", "hr", 0, 1),
            ("Remove from mailing lists and chat groups", "", "admin", None, 0, 2),
        ],
    },
    {
        "title": "HR Formalities",
        "exit_type": "all",
        "departments": [],
        "tasks": [
            ("Schedule the exit interview", "Book 30 minutes with the leaver and share the date.", "hr", None, -14, 7),
            ("Collect ID card and access badge", "", "hr", None, -1, 2),
            ("Prepare the relieving letter", "To be approved before it is issued.", "hr", "admin", 0, 5),
        ],
    },
    {
        "title": "Knowledge Transfer",
        "exit_type": "resignation",
        "departments": ["Development", "Designing"],
        "tasks": [
            ("Write the handover document", "Current work, pending items, contacts and access.", "employee", "manager", -21, 14),
            ("Walk the successor through the work", "At least two sessions.", "employee", "manager", -14, 10),
            ("Update project documentation", "", "employee", None, -10, 7),
        ],
    },
]

# name, department, gender, years of service
PEOPLE = [
    ("Aarav Mehta", "Development", "male", 3),
    ("Diya Kapoor", "Development", "female", 2),
    ("Kabir Nair", "Designing", "male", 4),
    ("Meera Iyer", "Accounts", "female", 5),
    ("Rohan Desai", "Business Development", "male", 2),
    ("Ishita Verma", "Development", "female", 1),
    ("Vikram Rao", "Accounts", "male", 6),
    ("Ananya Shah", "Development", "female", 2),
    ("Siddharth Jain", "Designing", "male", 3),
    ("Neha Bansal", "Business Development", "female", 1),
    ("Arjun Pillai", "Development", "male", 4),
    ("Tanvi Kulkarni", "Accounts", "female", 3),
    ("Yash Malhotra", "Designing", "male", 2),
]


def everything(model):
    """All rows of a model, across companies (not every model has a company filter)."""
    manager = model.objects
    return manager.entire() if hasattr(manager, "entire") else manager.all()


def days(n):
    return datetime.date.today() + datetime.timedelta(days=n)


class Command(BaseCommand):
    help = "Fill the Exit Process with sample data on a local PostgreSQL database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true", help="Remove everything this command created."
        )

    # ------------------------------------------------------------------ entry

    def handle(self, *args, **options):
        self.guard_database()
        admin_user = HorillaUser.objects.filter(is_superuser=True).order_by("pk").first()
        if admin_user is None:
            raise CommandError("There is no superuser to act as the administrator.")
        self.admin_user = admin_user
        self.admin = admin_user.employee_get
        # Models stamp created_by from the current request.
        _thread_locals.request = SimpleNamespace(user=admin_user, session={})

        if options["clear"]:
            with transaction.atomic():
                self.clear()
            return
        if everything(Employee).filter(email__endswith=DEMO_DOMAIN).exists():
            raise CommandError(
                "Sample data is already there. Run with --clear first to start again."
            )
        with transaction.atomic():
            self.seed()

    def guard_database(self):
        db = settings.DATABASES["default"]
        host = (db.get("HOST") or "").lower()
        if "postgresql" not in db["ENGINE"]:
            raise CommandError(f"Refusing to run: the database is {db['ENGINE']}, not PostgreSQL.")
        if host not in ("localhost", "127.0.0.1", "::1", ""):
            raise CommandError(
                f"Refusing to run: the database host is '{host}', not this computer."
            )
        self.stdout.write(
            f"Database: PostgreSQL '{db['NAME']}' on {host or 'localhost'} (local) - OK"
        )

    # ------------------------------------------------------------------ clear

    def clear(self):
        people = everything(Employee).filter(email__endswith=DEMO_DOMAIN)
        users = list(people.values_list("employee_user_id", flat=True))
        everything(ExitSurvey).filter(case_id__employee_id__in=people).delete()
        everything(ExitTask).filter(case_id__employee_id__in=people).delete()
        everything(ExitActivity).filter(employee_id__in=people).delete()
        everything(ResignationLetter).filter(employee_id__in=people).delete()
        everything(OffboardingEmployee).filter(employee_id__in=people).delete()
        everything(EmployeeNoticePolicy).filter(employee_id__in=people).delete()
        # Creating an employee also creates a payroll contract; it blocks deleting them.
        everything(Contract).filter(employee_id__in=people).delete()
        count = people.count()
        people.delete()
        HorillaUser.objects.filter(pk__in=[u for u in users if u]).delete()
        everything(TaskTemplate).filter(
            title__in=[g["title"] for g in TASK_GROUPS], phase="offboarding"
        ).delete()
        everything(NoticePeriodPolicy).filter(
            title__in=[p[0] for p in POLICIES], assignments__isnull=True
        ).delete()
        everything(ExitSurveyQuestion).filter(
            question__in=[q[0] for q in SURVEY_QUESTIONS]
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {count} sample people and their exit data."))

    # ------------------------------------------------------------------- seed

    def seed(self):
        self.company = everything(Company).first()
        if self.company is None:
            raise CommandError("There is no company yet.")
        self.today = datetime.date.today()
        self.manager_of = {
            "Development": everything(Employee).filter(email__icontains="sonali").first() or self.admin,
        }
        self.hr = (
            everything(Employee).filter(employee_work_info__department_id__department="Human Resource").first()
            or self.admin
        )
        self.make_policies()
        self.make_task_groups()
        self.make_survey_questions()
        self.people = {}
        for name, dept, gender, years in PEOPLE:
            self.people[name] = self.make_employee(name, dept, gender, years)
        self.make_cases()
        self.assign_policies()
        self.backdate()
        self.report()

    # --- configuration ---------------------------------------------------

    def make_policies(self):
        self.policies = {}
        for title, description, notice_days, is_default in POLICIES:
            policy = everything(NoticePeriodPolicy).filter(title=title).first()
            if policy is None:
                policy = NoticePeriodPolicy.objects.create(
                    title=title, description=description, days=notice_days,
                    is_default=is_default, company_id=self.company,
                )
            self.policies[title] = policy

    def person(self, kind):
        return {"admin": self.admin, "hr": self.hr}.get(kind)

    def make_task_groups(self):
        for spec in TASK_GROUPS:
            group = everything(TaskTemplate).filter(title=spec["title"], phase="offboarding").first()
            if group is not None:
                continue
            group = TaskTemplate.objects.create(
                title=spec["title"], phase="offboarding", company_id=self.company
            )
            for sequence, (title, description, owner, approver, offset, due_after) in enumerate(spec["tasks"], 1):
                task = TaskTemplateTask.objects.create(
                    template_id=group, title=title, description=description, sequence=sequence
                )
                owner_type = {"employee": "employee", "manager": "reporting_manager"}.get(owner, "specific")
                approver_type = (
                    "none" if approver is None
                    else "reporting_manager" if approver == "manager" else "specific"
                )
                ExitTaskConfig.objects.create(
                    task_id=task,
                    owner_type=owner_type,
                    owner_person=self.person(owner) if owner_type == "specific" else None,
                    approver_type=approver_type,
                    approver_person=self.person(approver) if approver_type == "specific" else None,
                    trigger_offset_days=offset,
                    due_after_days=due_after,
                    mandatory_attachment=False,
                )
            rule = ExitTaskGroupRule.objects.create(
                template_id=group, is_active=True, exit_type=spec["exit_type"]
            )
            if spec["departments"]:
                rule.include_departments.set(
                    everything(Department).filter(department__in=spec["departments"])
                )

    def make_survey_questions(self):
        for sequence, (text, kind, options, mandatory) in enumerate(SURVEY_QUESTIONS, 1):
            if everything(ExitSurveyQuestion).filter(question=text).exists():
                continue
            text, options = exit_survey.clean_question(text, kind, options)
            ExitSurveyQuestion.objects.create(
                company_id=self.company, question=text, type=kind, options=options,
                is_mandatory=mandatory, is_active=True, sequence=sequence,
            )

    # --- people ------------------------------------------------------------

    def make_employee(self, name, department_name, gender, years):
        first, last = name.split(" ", 1)
        email = f"{first}.{last}".lower().replace(" ", "") + DEMO_DOMAIN
        user = HorillaUser.objects.create(
            username=email, email=email, first_name=first, last_name=last, is_active=True
        )
        user.set_password(DEMO_PASSWORD)
        user.save()
        employee = Employee.objects.create(
            employee_first_name=first, employee_last_name=last, email=email,
            phone=f"98{abs(hash(name)) % 10**8:08d}", gender=gender, employee_user_id=user,
        )
        department = everything(Department).filter(department=department_name).first()
        job = everything(JobPosition).filter(department_id=department).first() or everything(JobPosition).first()
        info, _ = EmployeeWorkInformation.objects.get_or_create(employee_id=employee)
        info.company_id = self.company
        info.department_id = department
        info.job_position_id = job
        info.job_role_id = everything(JobRole).filter(job_position_id=job).first() or everything(JobRole).first()
        info.employee_type_id = everything(EmployeeType).first()
        info.reporting_manager_id = self.manager_of.get(department_name) or self.admin
        info.date_joining = self.today - datetime.timedelta(days=365 * years)
        info.save()
        return everything(Employee).get(pk=employee.pk)

    # --- cases ---------------------------------------------------------------

    def reason(self, title, exit_type="resignation"):
        return everything(ExitReasonOption).filter(title=title, exit_type=exit_type).first()

    def letter(self, who, *, ago, lwd, reason, exit_type="resignation", initiated_by=None,
               discussed=True, summary="", comment=""):
        employee = self.people[who]
        notice = self.today - datetime.timedelta(days=ago)
        option = self.reason(reason, exit_type)
        return ResignationLetter.objects.create(
            employee_id=employee, exit_type=exit_type,
            initiated_by=initiated_by or employee, notice_date=notice,
            title=f"Resignation - {employee.get_full_name()}" if exit_type == "resignation"
            else f"Termination - {employee.get_full_name()}",
            description=comment or "Please find my notice of resignation.",
            planned_to_leave_on=lwd, preferred_last_working_day=lwd,
            reason=option.title if option else reason, reason_option=option,
            discussed_with_manager=discussed, discussion_summary=summary,
            notice_period_days=(lwd - notice).days, notice_period_ends=lwd,
        )

    def accept(self, letter, lwd, comment="Accepted. Thank you for your contribution.", rehire=True):
        reviewer = letter.employee_id.get_reporting_manager() or self.admin
        return exit_service.review_request(
            letter, reviewer=reviewer, decision="accept", comment=comment,
            last_working_day=lwd, rehire_eligible=rehire,
        )

    def work_through(self, case, done=0, review=0, skip=0, overdue=0):
        """Move some of the case's tasks along. `done` counts tasks fully finished,
        `review` counts tasks left waiting for approval, `skip` skipped, `overdue` made late."""
        tasks = list(everything(ExitTask).filter(case_id=case).order_by("sequence", "pk"))
        pending = [t for t in tasks if t.status == "pending"]
        for task in pending[:skip]:
            exit_tasks.skip_task(task, self.admin, "Not applicable for this person.")
        for position, task in enumerate(pending[skip:]):
            owner = task.owner_id or self.admin
            if position < done + review:
                exit_tasks.complete_task(task, owner, note="Done.")
                task.refresh_from_db()
                if task.status == "review" and position < done:
                    exit_tasks.review_task(task, task.approver_id or self.admin, True, "Looks good.")
            elif overdue:
                everything(ExitTask).filter(pk=task.pk).update(
                    due_date=self.today - datetime.timedelta(days=overdue)
                )

    def survey(self, case, fill):
        survey = exit_survey.send_survey(case, self.admin)
        if not fill:
            return survey
        answers = {}
        for question in survey.questions:
            key, kind, options = str(question["id"]), question["type"], question["options"]
            if kind == "options":
                answers[key] = options[1 % len(options)]
            elif kind == "multiple":
                answers[key] = options[:2]
            elif kind == "rating":
                answers[key] = 4
            elif kind == "checkbox":
                answers[key] = "yes"
            elif kind == "textarea":
                answers[key] = "More chances to work on new technology and clearer growth paths."
            else:
                answers[key] = "Thank you all, it was a great journey."
        return exit_survey.submit(survey, case.employee_id, answers)

    def make_cases(self):
        # -- exits in progress, at different points ------------------------------
        lwd = days(10)
        case = self.accept(
            self.letter("Aarav Mehta", ago=20, lwd=lwd, reason="Explore Other Careers",
                        summary="Discussed; has accepted an offer elsewhere."), lwd)
        self.work_through(case, done=2, review=1)
        self.survey(case, fill=False)

        lwd = days(4)
        case = self.accept(
            self.letter("Diya Kapoor", ago=45, lwd=lwd, reason="Relocating",
                        summary="Moving to another city with family."), lwd)
        self.work_through(case, done=6, review=1)
        self.survey(case, fill=True)

        lwd = days(5)
        case = exit_service.start_exit_case(
            self.people["Kabir Nair"], exit_type="termination", last_working_day=lwd,
            notice_date=self.today - datetime.timedelta(days=2), rehire_eligible=False,
            rehire_comment="Left without completing the handover.")
        letter = self.letter("Kabir Nair", ago=2, lwd=lwd, reason="Misconduct",
                             exit_type="termination", initiated_by=self.admin, comment="Terminated for misconduct.")
        everything(ResignationLetter).filter(pk=letter.pk).update(status="approved", offboarding_employee_id=case)
        self.work_through(case, overdue=3)

        lwd = days(45)
        case = self.accept(
            self.letter("Meera Iyer", ago=3, lwd=lwd, reason="Personal Reasons",
                        summary="Family reasons; will serve the full notice."), lwd)
        self.work_through(case)

        # -- exited ---------------------------------------------------------------
        for who, ago_lwd, reason, notice_ago in (
            ("Rohan Desai", -12, "Explore Other Careers", 50),
            ("Ishita Verma", -30, "Relocating", 70),
        ):
            lwd = days(ago_lwd)
            case = self.accept(self.letter(who, ago=notice_ago, lwd=lwd, reason=reason,
                                           summary="Discussed and agreed."), lwd)
            self.work_through(case, done=12, skip=1 if who == "Ishita Verma" else 0)
            self.survey(case, fill=True)
            exit_service.mark_exited(case, self.admin)
            employee = self.people[who]
            everything(Employee).filter(pk=employee.pk).update(is_active=False)
            HorillaUser.objects.filter(pk=employee.employee_user_id_id).update(is_active=False)

        # -- accepted, then called off ---------------------------------------------
        lwd = days(25)
        case = self.accept(self.letter("Vikram Rao", ago=10, lwd=lwd, reason="Retirement"), lwd)
        exit_service.cancel_exit(case, self.admin, "Employee decided to stay after a revised offer.")

        # -- requests waiting for review ---------------------------------------------
        for who, ago, reason, summary in (
            ("Ananya Shah", 2, "Explore Other Careers", "Had a conversation with the manager; wants a change of domain."),
            ("Siddharth Jain", 6, "Personal Reasons", "Needs to move closer to family."),
            ("Neha Bansal", 0, "Other", ""),
        ):
            self.letter(who, ago=ago, lwd=days(30 - ago), reason=reason,
                        discussed=bool(summary), summary=summary)

        # -- reviewed without an exit ---------------------------------------------
        rejected = self.letter("Arjun Pillai", ago=8, lwd=days(20), reason="Explore Other Careers")
        exit_service.review_request(
            rejected, reviewer=rejected.employee_id.get_reporting_manager() or self.admin,
            decision="reject", comment="Critical release in progress; please revisit next month.")
        retained = self.letter("Tanvi Kulkarni", ago=12, lwd=days(18), reason="Personal Reasons")
        exit_service.review_request(
            retained, reviewer=self.admin, decision="retain",
            comment="Agreed a new role and compensation; the employee will stay.")
        withdrawn = self.letter("Yash Malhotra", ago=4, lwd=days(26), reason="Relocating")
        exit_service.withdraw_request(withdrawn, withdrawn.employee_id)

    def assign_policies(self):
        senior = self.policies["Senior staff notice"]
        probation = self.policies["Probation notice"]
        for who, policy in (("Aarav Mehta", senior), ("Vikram Rao", senior), ("Neha Bansal", probation)):
            EmployeeNoticePolicy.objects.get_or_create(
                employee_id=self.people[who], defaults={"policy_id": policy}
            )

    def backdate(self):
        """Dates the records as if they had been created when they happened."""
        for letter in everything(ResignationLetter).filter(employee_id__email__endswith=DEMO_DOMAIN):
            when = timezone.make_aware(datetime.datetime.combine(letter.notice_date, datetime.time(10, 30)))
            everything(ResignationLetter).filter(pk=letter.pk).update(created_at=when)
            if letter.reviewed_at:
                everything(ResignationLetter).filter(pk=letter.pk).update(
                    reviewed_at=when + datetime.timedelta(hours=5)
                )
            everything(ExitActivity).filter(letter_id=letter).update(created_at=when + datetime.timedelta(hours=5))

    def report(self):
        mine = everything(Employee).filter(email__endswith=DEMO_DOMAIN)
        self.stdout.write(self.style.SUCCESS("Sample exit data created:"))
        self.stdout.write(f"  people:             {mine.count()}")
        self.stdout.write(f"  requests:           {everything(ResignationLetter).filter(employee_id__in=mine).count()}")
        for status, label in ResignationLetter._meta.get_field("status").choices:
            n = everything(ResignationLetter).filter(employee_id__in=mine, status=status).count()
            if n:
                self.stdout.write(f"    {label:12} {n}")
        self.stdout.write(f"  exit cases:         {everything(OffboardingEmployee).filter(employee_id__in=mine).count()}")
        for status, label in OffboardingEmployee.EXIT_STATUSES:
            n = everything(OffboardingEmployee).filter(employee_id__in=mine, status=status).count()
            if n:
                self.stdout.write(f"    {label:20} {n}")
        self.stdout.write(f"  exit tasks:         {everything(ExitTask).filter(case_id__employee_id__in=mine).count()}")
        self.stdout.write(f"  surveys:            {everything(ExitSurvey).filter(case_id__employee_id__in=mine).count()}")
        self.stdout.write(f"  activity entries:   {everything(ExitActivity).filter(employee_id__in=mine).count()}")
