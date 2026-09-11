"""
recruitment/factories.py

factory_boy factories for the fixtures every recruitment/onboarding test
in this codebase was hand-building from scratch (see
recruitment/tests/test_intake_access_control.py, test_core_access_and_flow.py,
test_discovered_bugs.py, onboarding/tests.py) - a Company, Department,
JobPosition, Employee (with its auto-created User + EmployeeWorkInformation),
Recruitment (with its auto-created "Applied"/"Initial" stages), a Stage, and
a Candidate. Using these cuts new-test setUp boilerplate roughly in half and
keeps the auto-creation quirks (Employee.save() creating a User; Recruitment
creating a User; Recruitment.save() creating a User; Recruitment.save()
auto-creating "Applied"/"Initial" stages) handled in one place instead of
being rediscovered per test file.

Password for every EmployeeFactory-created login is "123qwe" (matching the
existing convention in the hand-written test files above).
"""

import factory
from factory.django import DjangoModelFactory

from base.models import Company, Department, JobPosition
from employee.models import Employee, EmployeeWorkInformation
from recruitment.models import Candidate, Recruitment, Stage

PASSWORD = "123qwe"


class CompanyFactory(DjangoModelFactory):
    class Meta:
        model = Company

    company = factory.Sequence(lambda n: f"Test Company {n}")
    address = "123 Test Street"
    country = "Testland"
    state = "Test State"
    city = "Test City"
    zip = "00000"


class DepartmentFactory(DjangoModelFactory):
    class Meta:
        model = Department

    department = factory.Sequence(lambda n: f"Test Department {n}")


class JobPositionFactory(DjangoModelFactory):
    class Meta:
        model = JobPosition

    job_position = factory.Sequence(lambda n: f"Test Role {n}")
    department_id = factory.SubFactory(DepartmentFactory)


class EmployeeFactory(DjangoModelFactory):
    """
    Employee.save() auto-creates a matching HorillaUser
    (employee_user_id) and a blank EmployeeWorkInformation row as a side
    effect - this factory finishes wiring both up afterward: sets a
    known password, clears the forced-password-change flag, and (if
    `company` is passed) attaches the work info to that company.

        EmployeeFactory(company=my_company, perms=["view_candidate"])
    """

    class Meta:
        model = Employee
        # Employee.save()'s own signal already creates a usable row for
        # any (first_name, email) pair, but re-running the factory with
        # the same email would otherwise violate uniqueness - not django
        # get_or_create since Employee has no natural unique key here
        # beyond email, and factory_boy's own dedup is per-factory-call.

    employee_first_name = factory.Sequence(lambda n: f"TestEmployee{n}")
    email = factory.Sequence(lambda n: f"test.employee{n}@example.com")
    phone = factory.Sequence(lambda n: f"90000{n:05d}")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # company/perms/reporting_manager aren't real Employee fields -
        # pop them before they reach Employee.objects.create(), then
        # apply them afterward now that Employee.save()'s own signal has
        # created the matching User + EmployeeWorkInformation row.
        company = kwargs.pop("company", None)
        perms = kwargs.pop("perms", None)
        reporting_manager = kwargs.pop("reporting_manager", None)

        employee = model_class.objects.create(*args, **kwargs)

        if company is not None or reporting_manager is not None:
            EmployeeWorkInformation.objects.filter(employee_id=employee).update(
                **({"company_id": company} if company is not None else {}),
                **({"reporting_manager_id": reporting_manager} if reporting_manager is not None else {}),
            )
        employee = Employee.objects.get(pk=employee.pk)
        user = employee.employee_user_id
        user.is_new_employee = False
        user.set_password(PASSWORD)
        user.save()
        if perms:
            from django.contrib.auth.models import Permission

            for codename in perms:
                user.user_permissions.add(Permission.objects.get(codename=codename))
        return employee


class RecruitmentFactory(DjangoModelFactory):
    """
    Recruitment.save() auto-creates "Applied"/"Initial" stages
    (recruitment/signals.py:create_initial_stage) - don't assume a fresh
    Recruitment has zero stages. If job_position is given, it's also
    added to open_positions (required for any Candidate to validly
    reference it - see Candidate.save()'s should_validate_job_position
    check).
    """

    class Meta:
        model = Recruitment

    title = factory.Sequence(lambda n: f"Test Recruitment {n}")
    company_id = factory.SubFactory(CompanyFactory)
    job_position_id = factory.SubFactory(JobPositionFactory)

    @factory.post_generation
    def open_position(self, create, extracted, **kwargs):
        if not create:
            return
        if self.job_position_id is not None:
            self.open_positions.add(self.job_position_id)


class StageFactory(DjangoModelFactory):
    class Meta:
        model = Stage

    recruitment_id = factory.SubFactory(RecruitmentFactory)
    stage = factory.Sequence(lambda n: f"Test Stage {n}")
    stage_type = "interview"
    sequence = factory.Sequence(lambda n: n + 10)


class CandidateFactory(DjangoModelFactory):
    """
    recruitment_id/job_position_id/stage_id default to a matching,
    consistent set (same recruitment throughout) rather than three
    independent SubFactories, since Candidate.save() validates
    job_position_id against recruitment_id.open_positions and stage_id
    isn't required to belong to recruitment_id but realistically should.
    """

    class Meta:
        model = Candidate

    name = factory.Sequence(lambda n: f"Test Candidate {n}")
    email = factory.Sequence(lambda n: f"test.candidate{n}@example.com")
    recruitment_id = factory.SubFactory(RecruitmentFactory)

    @factory.lazy_attribute
    def job_position_id(self):
        return self.recruitment_id.job_position_id

    @factory.lazy_attribute
    def stage_id(self):
        return Stage.objects.filter(recruitment_id=self.recruitment_id).order_by("sequence").first()
