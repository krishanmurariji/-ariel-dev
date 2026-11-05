"""
Portal roles and the capability matrix, ported 1:1 from ArielVertex
(Domain/Enums PortalRole, Application/Security/Permissions.cs + RoleGroups.cs).

Authorization is capability-based: roles grant capabilities, views ask for a
capability. Data-level scoping (e.g. which project) is layered on top later.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class PortalRole(models.TextChoices):
    SUPER_ADMIN = "super_admin", _("Super Admin")
    CEO_ADMIN = "ceo_admin", _("CEO")
    HR_MANAGER = "hr_manager", _("HR Manager")
    HR_DIRECTOR = "hr_director", _("HR Director")
    PROJECT_MANAGER = "project_manager", _("Project Manager")
    PROJECT_COORDINATOR = "project_coordinator", _("Project Coordinator")
    BUSINESS_DIRECTOR = "business_director", _("Business Director")
    BUSINESS_MANAGER = "business_manager", _("Business Manager")
    BUSINESS_PERSON = "business_person", _("Business Person")
    TECHNICAL_LEAD = "technical_lead", _("Technical Lead")
    SYSTEM_ADMIN = "system_admin", _("System Admin")
    FRONTDESK = "frontdesk", _("Front Desk")
    ACCOUNTANT = "accountant", _("Accountant")
    EMPLOYEE = "employee", _("Employee")


class ProjectRole(models.TextChoices):
    """Role a person holds on one specific project."""

    PROJECT_MANAGER = "project_manager", _("Project Manager")
    PROJECT_COORDINATOR = "project_coordinator", _("Project Coordinator")
    TECHNICAL_LEAD = "technical_lead", _("Technical Lead")
    BUSINESS_PERSON = "business_person", _("Business Person")
    DEVELOPER = "developer", _("Developer")
    QA = "qa", _("QA")
    MEMBER = "member", _("Member")


class Cap:
    """Capability catalogue (names identical to ArielVertex)."""

    PROJECTS_VIEW_ALL = "projects.view.all"
    PROJECTS_VIEW = "projects.view"
    PROJECTS_CREATE = "projects.create"
    PROJECTS_MANAGE = "projects.manage"
    MEMBERS_MANAGE = "members.manage"
    DOCUMENTS_UPLOAD = "documents.upload"
    DOCUMENTS_DELETE = "documents.delete"
    CALLS_MANAGE = "calls.manage"
    STATUS_SUBMIT = "status.submit"
    STATUS_VIEW_ALL = "status.view.all"
    REVIEWS_REQUEST = "reviews.request"
    REVIEWS_SCHEDULE = "reviews.schedule"
    REVIEWS_SUBMIT = "reviews.submit"
    FEEDBACK_SUBMIT = "feedback.submit"
    FEEDBACK_APPROVE = "feedback.approve"
    FEEDBACK_VIEW_ALL = "feedback.view.all"
    PERFORMANCE_VIEW_OWN = "performance.view.own"
    PERFORMANCE_VIEW_ALL = "performance.view.all"
    PERFORMANCE_PUBLISH = "performance.publish"
    RESOURCES_REQUEST = "resources.request"
    RESOURCES_MANAGE = "resources.manage"
    RESOURCES_VIEW_ALL = "resources.view.all"
    REPORTS_VIEW = "reports.view"
    EMPLOYEES_MANAGE = "employees.manage"
    ROLES_MANAGE = "roles.manage"
    SYNC_RUN = "sync.run"
    AUDIT_VIEW = "audit.view"
    ADMIN_SETTINGS = "admin.settings"
    EXPENSES_MANAGE = "expenses.manage"
    EXPENSES_APPROVE = "expenses.approve"
    EXPENSES_VIEW_ALL = "expenses.view.all"
    EXPENSES_CONFIGURE = "expenses.configure"
    PIP_VIEW = "pip.view"
    PIP_MANAGE = "pip.manage"
    CONFIG_MANAGE = "config.manage"
    MINUTES_MANAGE = "minutes.manage"
    CYCLES_MANAGE = "cycles.manage"
    APPRAISALS_MANAGE = "appraisals.manage"
    APPRAISALS_RELEASE = "appraisals.release"
    GOALS_ASSIGN = "goals.assign"
    GOALS_VIEW_ALL = "goals.view.all"
    PROMOTIONS_RECOMMEND = "promotions.recommend"
    PROMOTIONS_APPROVE = "promotions.approve"
    PROMOTIONS_MANAGE = "promotions.manage"
    LEARNING_MANAGE = "learning.manage"
    ANALYTICS_VIEW = "analytics.view"


ALL_CAPS = frozenset(v for k, v in vars(Cap).items() if not k.startswith("_"))

# Individual-contributor capabilities: never granted to the all-powerful admin roles.
_CONTRIBUTOR_ONLY = {Cap.STATUS_SUBMIT, Cap.PERFORMANCE_VIEW_OWN}
SUPER_ADMIN_GRANT = frozenset(ALL_CAPS - _CONTRIBUTOR_ONLY)

_DELIVERY = frozenset(
    {
        Cap.PROJECTS_VIEW,
        Cap.PROJECTS_CREATE,
        Cap.PROJECTS_MANAGE,
        Cap.MEMBERS_MANAGE,
        Cap.DOCUMENTS_UPLOAD,
        Cap.DOCUMENTS_DELETE,
        Cap.CALLS_MANAGE,
        Cap.STATUS_SUBMIT,
        Cap.STATUS_VIEW_ALL,
        Cap.REVIEWS_REQUEST,
        Cap.REVIEWS_SCHEDULE,
        Cap.REVIEWS_SUBMIT,
        Cap.FEEDBACK_SUBMIT,
        Cap.RESOURCES_REQUEST,
        Cap.REPORTS_VIEW,
        Cap.MINUTES_MANAGE,
        Cap.APPRAISALS_MANAGE,
        Cap.GOALS_ASSIGN,
        Cap.PROMOTIONS_RECOMMEND,
        Cap.LEARNING_MANAGE,
    }
)

_R = PortalRole
ROLE_CAPS = {
    _R.SUPER_ADMIN: SUPER_ADMIN_GRANT,
    # HR Director is a Super Admin equivalent (business decision in ArielVertex).
    _R.HR_DIRECTOR: SUPER_ADMIN_GRANT,
    _R.CEO_ADMIN: frozenset(
        {
            Cap.PROJECTS_VIEW_ALL,
            Cap.PROJECTS_VIEW,
            Cap.PERFORMANCE_VIEW_ALL,
            Cap.FEEDBACK_VIEW_ALL,
            Cap.RESOURCES_VIEW_ALL,
            Cap.REPORTS_VIEW,
            Cap.AUDIT_VIEW,
            Cap.EXPENSES_VIEW_ALL,
            Cap.PIP_VIEW,
            Cap.REVIEWS_REQUEST,
            Cap.FEEDBACK_SUBMIT,
            Cap.APPRAISALS_RELEASE,
            Cap.PROMOTIONS_APPROVE,
            Cap.GOALS_VIEW_ALL,
            Cap.ANALYTICS_VIEW,
        }
    ),
    _R.SYSTEM_ADMIN: frozenset(
        {
            Cap.SYNC_RUN,
            Cap.ADMIN_SETTINGS,
            Cap.AUDIT_VIEW,
            Cap.EMPLOYEES_MANAGE,
            Cap.PROJECTS_VIEW_ALL,
            Cap.PROJECTS_VIEW,
            Cap.EXPENSES_CONFIGURE,
            Cap.EXPENSES_VIEW_ALL,
            Cap.CONFIG_MANAGE,
        }
    ),
    _R.HR_MANAGER: frozenset(
        {
            Cap.PROJECTS_VIEW_ALL,
            Cap.PROJECTS_VIEW,
            Cap.EMPLOYEES_MANAGE,
            Cap.REVIEWS_REQUEST,
            Cap.FEEDBACK_SUBMIT,
            Cap.FEEDBACK_APPROVE,
            Cap.FEEDBACK_VIEW_ALL,
            Cap.PERFORMANCE_VIEW_ALL,
            Cap.PERFORMANCE_PUBLISH,
            Cap.RESOURCES_MANAGE,
            Cap.RESOURCES_VIEW_ALL,
            Cap.REPORTS_VIEW,
            Cap.EXPENSES_VIEW_ALL,
            Cap.PIP_VIEW,
            Cap.PIP_MANAGE,
            Cap.MINUTES_MANAGE,
            Cap.CYCLES_MANAGE,
            Cap.APPRAISALS_MANAGE,
            Cap.APPRAISALS_RELEASE,
            Cap.GOALS_ASSIGN,
            Cap.GOALS_VIEW_ALL,
            Cap.PROMOTIONS_MANAGE,
            Cap.LEARNING_MANAGE,
            Cap.ANALYTICS_VIEW,
        }
    ),
    _R.ACCOUNTANT: frozenset(
        {Cap.PROJECTS_VIEW, Cap.EXPENSES_APPROVE, Cap.EXPENSES_VIEW_ALL}
    ),
    _R.FRONTDESK: frozenset({Cap.PROJECTS_VIEW, Cap.EXPENSES_MANAGE}),
    _R.PROJECT_MANAGER: _DELIVERY,
    _R.PROJECT_COORDINATOR: _DELIVERY,
    _R.TECHNICAL_LEAD: frozenset(
        {
            Cap.PROJECTS_VIEW,
            Cap.DOCUMENTS_UPLOAD,
            Cap.STATUS_VIEW_ALL,
            Cap.REVIEWS_SUBMIT,
        }
    ),
    _R.BUSINESS_DIRECTOR: frozenset({Cap.PROJECTS_VIEW}),
    _R.BUSINESS_MANAGER: frozenset({Cap.PROJECTS_VIEW}),
    _R.BUSINESS_PERSON: frozenset({Cap.PROJECTS_VIEW}),
    _R.EMPLOYEE: frozenset(
        {Cap.PROJECTS_VIEW, Cap.STATUS_SUBMIT, Cap.PERFORMANCE_VIEW_OWN}
    ),
}


def capabilities_for(role) -> frozenset:
    """Capabilities a role grants; unknown roles fall back to Employee (as ArielVertex)."""
    return ROLE_CAPS.get(role, ROLE_CAPS[_R.EMPLOYEE])


def is_hr(role) -> bool:
    return role in (_R.HR_MANAGER, _R.HR_DIRECTOR)


def is_management(role) -> bool:
    return role in (_R.SUPER_ADMIN, _R.CEO_ADMIN)


def is_delivery(role) -> bool:
    return role in (_R.PROJECT_MANAGER, _R.PROJECT_COORDINATOR)


def is_business(role) -> bool:
    return role in (_R.BUSINESS_DIRECTOR, _R.BUSINESS_MANAGER, _R.BUSINESS_PERSON)


def is_privileged(role) -> bool:
    return role in (_R.SUPER_ADMIN, _R.SYSTEM_ADMIN)


def dashboard_for(role) -> str:
    """Which dashboard a role lands on."""
    if role in (_R.SUPER_ADMIN, _R.CEO_ADMIN, _R.SYSTEM_ADMIN):
        return "admin"
    if is_hr(role):
        return "hr"
    if is_delivery(role):
        return "delivery"
    if is_business(role):
        return "business"
    return "employee"
