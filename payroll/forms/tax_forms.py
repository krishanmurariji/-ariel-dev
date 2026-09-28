"""
Forms for handling payroll-related operations.

This module provides Django ModelForms for creating and managing payroll-related data,
including filing status, tax brackets, and federal tax records.

The forms in this module inherit from the Django `forms.ModelForm` class and customize
the widget attributes to enhance the user interface and provide a better user experience.

"""

from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from payroll.methods import federal_tax
from payroll.methods.safe_tax_code import TaxCodeValidationError, validate_tax_code
from payroll.models.models import FilingStatus
from payroll.models.tax_models import TaxBracket


class FilingStatusForm(ModelForm):
    """Form for creating and updating filing status."""

    cols = {
        "filing_status": 12,
        "based_on": 12,
        "description": 12,
    }

    class Meta:
        """Meta options for the form."""

        model = FilingStatus
        fields = "__all__"
        exclude = ["is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Python tax formulas are disabled (see
        # payroll.methods.safe_tax_code.run_tax_code), so the "Python Code"
        # switch and its editor are never offered - tax is always
        # calculated from the filing status's tax brackets. Both fields are
        # left out entirely (not just hidden) so they can't be posted
        # either; the model defaults (use_py=False, no code) apply.
        self.fields.pop("use_py", None)
        self.fields.pop("python_code", None)

        # Original behaviour, disabled: on create it showed the use_py
        # toggle (revealing an embedded third-party code editor) and seeded
        # python_code with the default template; on edit it removed both.
        #
        # attrs: dict = self.fields["use_py"].widget.attrs
        # self.fields["python_code"].required = False
        # attrs["onchange"] = "...show/hide #oc-editor..."
        # if self.instance.pk is None:
        #     self.instance.python_code = federal_tax.CODE
        # else:
        #     del self.fields["use_py"]
        #     del self.fields["python_code"]

    # clean_python_code (validate_tax_code at save time) is disabled along
    # with the field itself - nothing executes stored code any more.


class TaxBracketForm(ModelForm):
    """Form for creating and updating tax bracket."""

    cols = {"min_income": 12, "max_income": 12, "tax_rate": 12}

    class Meta:
        """Meta options for the form."""

        model = TaxBracket
        fields = "__all__"
        exclude = ["is_active"]
        widgets = {
            "filing_status_id": forms.HiddenInput(),
        }
