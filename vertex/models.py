"""
vertex/models.py

Foundation models. Employees, departments, managers and designations already
exist in Ariel HR, so Vertex only stores what is missing:

* VertexProfile  - portal role, skills, avatar colour (ArielVertex ``User`` extras)
* DepartmentCode - short code for a department (ArielVertex ``Department.Code``)

Manager       -> EmployeeWorkInformation.reporting_manager_id
Department    -> EmployeeWorkInformation.department_id
Designation   -> EmployeeWorkInformation.job_position_id
Joining date  -> EmployeeWorkInformation.date_joining
Status        -> Employee.is_active
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from vertex.roles import PortalRole


class VertexProfile(models.Model):
    employee = models.OneToOneField(
        "employee.Employee",
        on_delete=models.CASCADE,
        related_name="vertex_profile",
    )
    portal_role = models.CharField(
        max_length=32,
        choices=PortalRole.choices,
        default=PortalRole.EMPLOYEE,
        verbose_name=_("Portal role"),
    )
    skills = models.CharField(
        max_length=500, blank=True, default="", help_text=_("Comma separated")
    )
    avatar_color = models.CharField(max_length=9, default="#1E7FD4")
    profile_managed_locally = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Vertex profile")
        verbose_name_plural = _("Vertex profiles")

    def __str__(self):
        return f"{self.employee} ({self.get_portal_role_display()})"

    @property
    def skill_list(self):
        return [s.strip() for s in self.skills.split(",") if s.strip()]


class DepartmentCode(models.Model):
    department = models.OneToOneField(
        "base.Department", on_delete=models.CASCADE, related_name="vertex_code"
    )
    code = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return f"{self.code} - {self.department}"
