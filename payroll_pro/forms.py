"""
payroll_pro/forms.py
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from payroll.models.models import Reimbursement
from payroll_pro import services
from payroll_pro.models import (
    EmployeeSalary,
    PayrollProAutoSetting,
    SalaryComponent,
    SalaryStructure,
    TaxDeclaration,
    TaxRegime,
)

TEXT_ATTRS = {"class": "oh-input w-full"}
SELECT_ATTRS = {"class": "oh-select w-full"}


def _style(fields):
    for _name, field in fields.items():
        widget = field.widget
        if isinstance(widget, (forms.CheckboxInput,)):
            widget.attrs.update({"class": "oh-switch__checkbox"})
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            widget.attrs.update(SELECT_ATTRS)
        else:
            widget.attrs.update(TEXT_ATTRS)


class TaxRegimeForm(forms.ModelForm):
    """Create / update a tax regime."""

    class Meta:
        model = TaxRegime
        fields = [
            "name",
            "description",
            "standard_deduction",
            "rebate_limit",
            "cess_pct",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["name"].widget.attrs.update({"maxlength": 100})
        self.fields["standard_deduction"].widget.attrs.update({"min": 0})
        self.fields["rebate_limit"].widget.attrs.update({"min": 0})
        self.fields["cess_pct"].widget.attrs.update({"min": 0, "max": 100})
        if locked:
            for name, field in self.fields.items():
                if name not in ("name", "description"):
                    field.disabled = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError(_("Regime name is required."))
        duplicate = TaxRegime.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError(
                _("A tax regime with this name already exists.")
            )
        return name

    def clean_cess_pct(self):
        value = self.cleaned_data.get("cess_pct")
        if value is not None and value > 100:
            raise forms.ValidationError(_("Cess % cannot exceed 100."))
        return value


class SalaryStructureForm(forms.ModelForm):
    """Create / update a salary structure (rulebook)."""

    class Meta:
        model = SalaryStructure
        fields = [
            "name",
            "description",
            "tax_regime_id",
            "gross_mode",
            "gross_pct",
            "gross_formula",
            "pf_enabled",
            "pf_employee_pct",
            "pf_employer_pct",
            "pf_ceiling_enabled",
            "pf_wage_ceiling",
            "pt_enabled",
            "pt_threshold",
            "pt_amount",
            "gratuity_enabled",
            "gratuity_pct",
            "tds_enabled",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    PLACEHOLDERS = {
        "name": _("e.g. Standard Structure - India"),
        "description": _("Briefly describe who this structure is for..."),
        "gross_pct": _("100"),
        "gross_formula": _("e.g. CTC * 0.9 - 1600"),
        "pf_employee_pct": _("12"),
        "pf_employer_pct": _("12"),
        "pf_wage_ceiling": _("15000"),
        "pt_threshold": _("15000"),
        "pt_amount": _("200"),
        "gratuity_pct": _("4.81"),
    }

    def __init__(self, *args, locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["tax_regime_id"].queryset = TaxRegime.objects.all()
        self.fields["name"].widget.attrs.update({"maxlength": 150})
        for name, placeholder in self.PLACEHOLDERS.items():
            self.fields[name].widget.attrs.setdefault("placeholder", placeholder)
        for name in ("gross_pct", "pf_employee_pct", "pf_employer_pct", "gratuity_pct"):
            self.fields[name].widget.attrs.update({"min": 0, "max": 100})
        for name in ("pf_wage_ceiling", "pt_threshold", "pt_amount"):
            self.fields[name].widget.attrs.update({"min": 0})

        # The UI never offers "auto" (back out employer PF from CTC) —
        # SalaryStructure.GROSS_MODE_CHOICES keeps it only as a defensive
        # fallback for services.derive_gross().
        self.fields["gross_mode"].choices = [
            choice
            for choice in SalaryStructure.GROSS_MODE_CHOICES
            if choice[0] != "auto"
        ]

        # pf_enabled/pt_enabled/tds_enabled/gratuity_enabled/pf_ceiling_enabled
        # stay on BooleanField's default CheckboxInput — Django's CheckboxInput
        # already treats an absent POST key as False (value_from_datadict), so
        # it round-trips correctly. _style() above gave it the oh-switch__checkbox
        # class; the template wraps each one in a `.oh-switch` div to render it
        # as a native toggle switch instead of a dropdown.

        # These values only matter while their master toggle is on, so a
        # stripped-down structure (PF/PT/Gratuity all off) must be creatable
        # without filling them in — services.py already ignores them whenever
        # the corresponding *_enabled flag is off. clean() below fills a
        # sensible default if left blank, since the model fields aren't
        # nullable.
        for name in self._CONDITIONAL_DEFAULTS:
            self.fields[name].required = False

        if locked:
            for name, field in self.fields.items():
                if name not in ("name", "description"):
                    field.disabled = True

    _CONDITIONAL_DEFAULTS = {
        "pf_employee_pct": 12,
        "pf_employer_pct": 12,
        "pf_wage_ceiling": 15000,
        "pt_threshold": 15000,
        "pt_amount": 200,
        "gratuity_pct": 4.81,
    }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError(_("Structure name is required."))
        duplicate = SalaryStructure.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError(
                _("A salary structure with this name already exists.")
            )
        return name

    def clean(self):
        cleaned_data = super().clean()
        for name, default in self._CONDITIONAL_DEFAULTS.items():
            if cleaned_data.get(name) is None:
                cleaned_data[name] = default

        gross_mode = cleaned_data.get("gross_mode")
        if gross_mode == "pct":
            gross_pct = cleaned_data.get("gross_pct")
            if gross_pct is None or gross_pct <= 0 or gross_pct > 100:
                self.add_error(
                    "gross_pct", _("Gross % must be between 0 and 100.")
                )
        elif gross_mode == "formula":
            formula = (cleaned_data.get("gross_formula") or "").strip()
            if not formula:
                self.add_error(
                    "gross_formula",
                    _("Enter a formula, or switch the gross derivation rule."),
                )
            elif services.safe_eval_gross_formula(formula, 100000) is None:
                self.add_error(
                    "gross_formula",
                    _(
                        "Formula is invalid — use only numbers, + - * / ( ) "
                        "and the variable CTC."
                    ),
                )
            else:
                cleaned_data["gross_formula"] = formula

        if cleaned_data.get("pf_enabled"):
            for field_name in ("pf_employee_pct", "pf_employer_pct"):
                value = cleaned_data.get(field_name)
                if value is not None and (value < 0 or value > 100):
                    self.add_error(field_name, _("Must be between 0 and 100."))

        if cleaned_data.get("gratuity_enabled"):
            value = cleaned_data.get("gratuity_pct")
            if value is not None and (value < 0 or value > 100):
                self.add_error("gratuity_pct", _("Must be between 0 and 100."))

        return cleaned_data


class SalaryComponentForm(forms.ModelForm):
    """Add / edit one earning component of a structure."""

    class Meta:
        model = SalaryComponent
        fields = ["name", "component_type", "value", "sequence"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class EmployeeSalaryForm(forms.ModelForm):
    """
    Assign an employee to a structure / record a pay revision.
    Always creates a new revision row — history stays intact.
    """

    class Meta:
        model = EmployeeSalary
        fields = [
            "employee_id",
            "salary_structure_id",
            "annual_ctc",
            "effective_date",
            "note",
        ]
        widgets = {"effective_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["employee_id"].queryset = Employee.objects.filter(is_active=True)
        self.fields["annual_ctc"].widget.attrs.update({"min": 0.01})

    def clean_annual_ctc(self):
        value = self.cleaned_data.get("annual_ctc")
        if value is not None and value <= 0:
            raise forms.ValidationError(_("Annual CTC must be greater than zero."))
        return value

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get("employee_id")
        effective_date = cleaned_data.get("effective_date")
        if employee and effective_date:
            work_info = getattr(employee, "employee_work_info", None)
            joining_date = getattr(work_info, "date_joining", None)
            if joining_date and effective_date < joining_date:
                self.add_error(
                    "effective_date",
                    _(
                        "%(name)s joined on %(joining)s — effective date "
                        "can't be earlier than that."
                    )
                    % {"name": employee.get_full_name(), "joining": joining_date},
                )
            elif EmployeeSalary.objects.filter(
                employee_id=employee, effective_date=effective_date
            ).exists():
                self.add_error(
                    "effective_date",
                    _(
                        "%(name)s already has a salary revision effective "
                        "on %(date)s."
                    )
                    % {"name": employee.get_full_name(), "date": effective_date},
                )
        return cleaned_data


class BulkEmployeeSalaryForm(forms.Form):
    """
    First-time salary assignment for one or many employees at once. Only
    offers employees not already on payroll — an employee who already has
    a revision belongs in Pay Revisions instead, keeping the two flows
    segregated.
    """

    employee_ids = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(
            is_active=True, pro_salary_revisions__isnull=True
        ).distinct(),
        label=_("Employees"),
        widget=forms.SelectMultiple,
    )
    salary_structure_id = forms.ModelChoiceField(
        queryset=SalaryStructure.objects.all(),
        label=_("Salary Structure"),
    )
    annual_ctc = forms.FloatField(label=_("Annual CTC"), min_value=0.01)
    effective_date = forms.DateField(
        label=_("Effective Date"), widget=forms.DateInput(attrs={"type": "date"})
    )
    note = forms.CharField(label=_("Note"), max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["employee_ids"].widget.attrs.update(
            {"class": "oh-select w-full", "size": "8"}
        )

    def clean(self):
        cleaned_data = super().clean()
        employees = cleaned_data.get("employee_ids")
        effective_date = cleaned_data.get("effective_date")
        if employees and effective_date:
            for employee in employees:
                work_info = getattr(employee, "employee_work_info", None)
                joining_date = getattr(work_info, "date_joining", None)
                if joining_date and effective_date < joining_date:
                    self.add_error(
                        "effective_date",
                        _(
                            "%(name)s joined on %(joining)s — effective date "
                            "can't be earlier than that."
                        )
                        % {"name": employee.get_full_name(), "joining": joining_date},
                    )
                    break
                if EmployeeSalary.objects.filter(
                    employee_id=employee, effective_date=effective_date
                ).exists():
                    self.add_error(
                        "effective_date",
                        _(
                            "%(name)s already has a salary revision effective "
                            "on %(date)s."
                        )
                        % {"name": employee.get_full_name(), "date": effective_date},
                    )
                    break
        return cleaned_data

    def save(self):
        """Create one EmployeeSalary revision per selected employee. No
        tax regime override is collected here — TDS always follows
        whatever regime is linked to the salary structure."""
        data = self.cleaned_data
        created = []
        for employee in data["employee_ids"]:
            created.append(
                EmployeeSalary.objects.create(
                    employee_id=employee,
                    salary_structure_id=data["salary_structure_id"],
                    annual_ctc=data["annual_ctc"],
                    effective_date=data["effective_date"],
                    note=data["note"],
                )
            )
        return created


class ReimbursementRequestForm(forms.ModelForm):
    """
    Employee self-service reimbursement request — reuses the stock
    payroll.Reimbursement model so approvals stay consistent with the rest
    of the application (e.g. Allowance creation on approval).
    """

    class Meta:
        model = Reimbursement
        fields = ["title", "amount", "allowance_on", "attachment", "description"]
        widgets = {"allowance_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
        self.fields["attachment"].widget.attrs.update({"class": "oh-input w-full"})
        self.fields["attachment"].required = True


class TaxDeclarationForm(forms.ModelForm):
    """Employee self-service investment declaration."""

    class Meta:
        model = TaxDeclaration
        fields = ["section_80c", "section_80d", "proof_submitted"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class PayrollProAutoSettingForm(forms.ModelForm):
    """Automatic monthly payroll run configuration."""

    class Meta:
        model = PayrollProAutoSetting
        fields = [
            "company_id",
            "run_day",
            "auto_run",
            "auto_finalize",
            "auto_email_payslips",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
