"""
offboarding/tests.py

Regression coverage for the ResignationLetter.description stored-XSS fix.
"""

from datetime import date

from django.test import TestCase

from base.models import Company
from employee.models import Employee, EmployeeWorkInformation
from offboarding.forms import ResignationLetterForm


class ResignationLetterDescriptionSanitizationTests(TestCase):
    """
    ResignationLetter.description is a Summernote-edited rich-text field
    rendered with |safe across several templates (the resignation detail
    views HR/managers use to review a request) - unsanitized input was a
    stored-XSS vector: any employee submitting a resignation could embed
    <script> that would run in a reviewer's browser. Fixed by sanitizing
    the field with bleach on save, in ResignationLetterForm
    .clean_description() (this web path) and
    ResignationLetterSerializer.validate_description() (the API write
    path - see horilla_api/api_serializers/offboarding/serializers.py).
    """

    def setUp(self):
        self.company = Company.objects.create(
            company="ResignSanitizeCo", address="x", country="x", state="x",
            city="x", zip="00000",
        )
        self.employee = Employee.objects.create(
            employee_first_name="Resign Sanitize Emp",
            email="resign.sanitize@example.com", phone="9880000001",
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.employee).update(
            company_id=self.company
        )

    def _form(self, description):
        return ResignationLetterForm(data={
            "employee_id": self.employee.pk,
            "title": "Resigning",
            "description": description,
            "planned_to_leave_on": date.today(),
            "status": "requested",
        })

    def test_script_tag_is_stripped_from_description(self):
        form = self._form("<p>Goodbye</p><script>alert('xss')</script>")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("<script", form.cleaned_data["description"])
        self.assertIn("Goodbye", form.cleaned_data["description"])

    def test_event_handler_attribute_is_stripped(self):
        form = self._form('<p onclick="alert(1)">Click me</p>')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("onclick", form.cleaned_data["description"])

    def test_allowed_formatting_tags_survive(self):
        form = self._form("<p><strong>Bold</strong> and <em>italic</em></p>")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("<strong>", form.cleaned_data["description"])
        self.assertIn("<em>", form.cleaned_data["description"])
