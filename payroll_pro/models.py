"""
payroll_pro/models.py

Database design notes
=====================
History integrity is guaranteed by two rules:

1. Employee salary assignments (``EmployeeSalary``) are append-only revision
   rows. Revising CTC / structure / regime creates a new row; old rows are
   never mutated, giving a full salary timeline per employee.

2. When a payroll run is finalized, every ``PayrollRunRecord`` freezes a
   complete JSON snapshot (``computed``) of everything that produced the
   numbers: the salary-structure definition, every component and its value,
   the tax-regime slabs, attendance / LOP counts and each calculated amount.
   Payslips are always rendered from this snapshot — later edits to a
   SalaryStructure or TaxRegime can never alter historical payslips.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel

EMPLOYEE_COMPANY_FIELD = "employee_id__employee_work_info__company_id"


def min_zero(value):
    """Validation: value must not be negative."""
    if value is not None and value < 0:
        raise ValidationError(_("Value must be greater than or equal to zero"))


# ---------------------------------------------------------------------------
# 1. Tax Regime
# ---------------------------------------------------------------------------
class TaxRegime(HorillaModel):
    """
    An income-tax regime (e.g. Indian "New Regime" / "Old Regime") with its
    slab table and deduction rules. Referenced by SalaryStructure and can be
    overridden per employee on their salary assignment.
    """

    name = models.CharField(max_length=100, verbose_name=_("Regime Name"))
    description = models.TextField(
        blank=True, null=True, max_length=255, verbose_name=_("Description")
    )
    standard_deduction = models.FloatField(
        default=0,
        validators=[min_zero],
        verbose_name=_("Standard Deduction (per year)"),
    )
    rebate_limit = models.FloatField(
        default=0,
        validators=[min_zero],
        verbose_name=_("Rebate Limit"),
    )
    cess_pct = models.FloatField(
        default=4,
        validators=[min_zero],
        verbose_name=_("Cess %"),
    )
    company_id = models.ForeignKey(
        Company,
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager()

    class Meta:
        ordering = ["-id"]
        verbose_name = _("Tax Regime")
        verbose_name_plural = _("Tax Regimes")

    def __str__(self) -> str:
        return str(self.name)

    def ordered_slabs(self):
        """Slabs ordered by lower bound."""
        return self.slabs.all().order_by("min_amount")

    def current_employee_count(self) -> int:
        """Employees currently taxed under this regime (override or via
        their structure's default) — historical/superseded revisions don't
        count, matching SalaryStructure.assigned_employee_count()."""
        return sum(
            1
            for revision in EmployeeSalary.current_salaries()
            if revision.effective_tax_regime()
            and revision.effective_tax_regime().pk == self.pk
        )

    @property
    def is_locked(self) -> bool:
        """A regime currently driving live payroll can't be edited — only
        duplicated — so past-linked structures/employees are never silently
        recalculated under changed rules."""
        return self.pk is not None and self.current_employee_count() > 0

    def to_snapshot(self) -> dict:
        """JSON-safe frozen copy of the regime used in payslip snapshots."""
        return {
            "id": self.pk,
            "name": self.name,
            "standard_deduction": self.standard_deduction,
            "rebate_limit": self.rebate_limit,
            "cess_pct": self.cess_pct,
            "slabs": [
                {
                    "min_amount": slab.min_amount,
                    "max_amount": slab.max_amount,
                    "rate_pct": slab.rate_pct,
                }
                for slab in self.ordered_slabs()
            ],
        }


class TaxSlab(HorillaModel):
    """
    A single slab row of a tax regime. ``max_amount`` empty means "and above".
    """

    tax_regime_id = models.ForeignKey(
        TaxRegime,
        on_delete=models.CASCADE,
        related_name="slabs",
        verbose_name=_("Tax Regime"),
    )
    min_amount = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("From (yearly)")
    )
    max_amount = models.FloatField(
        null=True,
        blank=True,
        validators=[min_zero],
        verbose_name=_("To (yearly)"),
        help_text=_("Leave empty for 'and above'."),
    )
    rate_pct = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Tax Rate %")
    )

    class Meta:
        ordering = ["min_amount"]
        verbose_name = _("Tax Slab")
        verbose_name_plural = _("Tax Slabs")

    def __str__(self) -> str:
        upper = self.max_amount if self.max_amount is not None else _("above")
        return f"{self.tax_regime_id} : {self.min_amount} - {upper} @ {self.rate_pct}%"

    def clean(self):
        super().clean()
        if self.max_amount is not None and self.max_amount <= self.min_amount:
            raise ValidationError(
                {"max_amount": _("'To' must be greater than 'From'.")}
            )


# ---------------------------------------------------------------------------
# 2. Salary Structure
# ---------------------------------------------------------------------------
class SalaryStructure(HorillaModel):
    """
    A full pay rulebook: how monthly gross is derived from CTC, the ordered
    earning components, and statutory PF / PT / TDS configuration. The linked
    ``tax_regime_id`` is the default regime for employees on this structure.
    """

    # "auto" is kept only as a defensive fallback for `services.derive_gross()` —
    # the UI (SalaryStructureForm) intentionally never offers it as a choice.
    GROSS_MODE_CHOICES = [
        ("auto", _("Back out employer PF from CTC")),
        ("pct", _("Fixed % of Monthly CTC")),
        ("formula", _("Custom formula")),
    ]

    name = models.CharField(max_length=150, verbose_name=_("Structure Name"))
    description = models.TextField(
        blank=True, null=True, max_length=500, verbose_name=_("Description")
    )
    tax_regime_id = models.ForeignKey(
        TaxRegime,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="salary_structures",
        verbose_name=_("Tax Regime"),
        help_text=_(
            "Default tax regime for employees on this structure. "
            "Required when TDS is enabled."
        ),
    )
    gross_mode = models.CharField(
        max_length=20,
        choices=GROSS_MODE_CHOICES,
        default="pct",
        verbose_name=_("Gross Derivation Rule"),
    )
    gross_pct = models.FloatField(
        default=100,
        validators=[min_zero],
        verbose_name=_("Gross as % of Monthly CTC"),
    )
    gross_formula = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_("Custom Gross Formula"),
        help_text=_("Allowed: numbers, + - * / ( ) and the variable CTC (monthly)."),
    )
    include_employer_pf_in_ctc = models.BooleanField(
        default=True, verbose_name=_("Employer PF included in CTC")
    )
    # --- Provident Fund ---
    pf_enabled = models.BooleanField(default=True, verbose_name=_("PF Enabled"))
    pf_employee_pct = models.FloatField(
        default=12, validators=[min_zero], verbose_name=_("Employee PF % (of Basic)")
    )
    pf_employer_pct = models.FloatField(
        default=12, validators=[min_zero], verbose_name=_("Employer PF % (of Basic)")
    )
    pf_ceiling_enabled = models.BooleanField(
        default=False, verbose_name=_("Restrict PF to Wage Ceiling")
    )
    pf_wage_ceiling = models.FloatField(
        default=15000, validators=[min_zero], verbose_name=_("PF Wage Ceiling (monthly)")
    )
    # --- Professional Tax ---
    pt_enabled = models.BooleanField(default=True, verbose_name=_("PT Enabled"))
    pt_threshold = models.FloatField(
        default=15000,
        validators=[min_zero],
        verbose_name=_("PT Slab Threshold (monthly gross)"),
    )
    pt_amount = models.FloatField(
        default=200, validators=[min_zero], verbose_name=_("PT Amount")
    )
    # --- Gratuity (employer cost accrual only — never deducted from pay) ---
    gratuity_enabled = models.BooleanField(
        default=False, verbose_name=_("Gratuity Enabled")
    )
    gratuity_pct = models.FloatField(
        default=4.81,
        validators=[min_zero],
        verbose_name=_("Gratuity accrual % (of Basic)"),
        help_text=_(
            "Standard statutory formula: Basic × 15 ÷ 26 per year of service ≈ "
            "4.81% of Basic per month. This is an employer cost accrual — not "
            "deducted from employee pay."
        ),
    )
    # --- TDS ---
    tds_enabled = models.BooleanField(default=True, verbose_name=_("TDS Enabled"))
    company_id = models.ForeignKey(
        Company,
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager()

    class Meta:
        ordering = ["-id"]
        verbose_name = _("Salary Structure")
        verbose_name_plural = _("Salary Structures")

    def __str__(self) -> str:
        return str(self.name)

    def clean(self):
        super().clean()
        if self.tds_enabled and not self.tax_regime_id:
            raise ValidationError(
                {"tax_regime_id": _("Select a tax regime when TDS is enabled.")}
            )

    def ordered_components(self):
        """Components in calculation order."""
        return self.components.all().order_by("sequence", "id")

    def basic_component(self):
        """The component flagged as Basic."""
        return self.ordered_components().filter(is_basic=True).first()

    def assigned_employee_count(self) -> int:
        """Number of employees whose current salary uses this structure."""
        return sum(
            1
            for salary in EmployeeSalary.current_salaries()
            if salary.salary_structure_id_id == self.pk
        )

    @property
    def is_locked(self) -> bool:
        """A structure currently assigned to someone can't be edited — only
        duplicated — so their pay isn't silently recalculated under changed
        rules. Structures with only historical (superseded) assignments stay
        editable, since old payslips are frozen snapshots regardless."""
        return self.pk is not None and self.assigned_employee_count() > 0

    def to_snapshot(self) -> dict:
        """JSON-safe frozen copy of the structure for payslip snapshots."""
        return {
            "id": self.pk,
            "name": self.name,
            "gross_mode": self.gross_mode,
            "gross_pct": self.gross_pct,
            "gross_formula": self.gross_formula,
            "include_employer_pf_in_ctc": self.include_employer_pf_in_ctc,
            "pf": {
                "enabled": self.pf_enabled,
                "employee_pct": self.pf_employee_pct,
                "employer_pct": self.pf_employer_pct,
                "ceiling_enabled": self.pf_ceiling_enabled,
                "wage_ceiling": self.pf_wage_ceiling,
            },
            "pt": {
                "enabled": self.pt_enabled,
                "threshold": self.pt_threshold,
                "amount": self.pt_amount,
            },
            "gratuity": {
                "enabled": self.gratuity_enabled,
                "pct": self.gratuity_pct,
            },
            "tds_enabled": self.tds_enabled,
            "tax_regime": (
                self.tax_regime_id.to_snapshot() if self.tax_regime_id else None
            ),
            "components": [
                {
                    "id": comp.pk,
                    "name": comp.name,
                    "component_type": comp.component_type,
                    "value": comp.value,
                    "is_basic": comp.is_basic,
                }
                for comp in self.ordered_components()
            ],
        }


class SalaryComponent(HorillaModel):
    """
    One ordered earning component of a salary structure.
    """

    COMPONENT_TYPE_CHOICES = [
        ("pct_gross", _("% of Gross")),
        ("pct_basic", _("% of Basic")),
        ("flat", _("Flat amount / month")),
        ("balance", _("Balancing figure")),
    ]

    structure_id = models.ForeignKey(
        SalaryStructure,
        on_delete=models.CASCADE,
        related_name="components",
        verbose_name=_("Salary Structure"),
    )
    name = models.CharField(max_length=150, verbose_name=_("Component"))
    component_type = models.CharField(
        max_length=20,
        choices=COMPONENT_TYPE_CHOICES,
        default="flat",
        verbose_name=_("Calculation Type"),
    )
    value = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Value")
    )
    is_basic = models.BooleanField(
        default=False,
        verbose_name=_("Is Basic"),
        help_text=_("Basic is the base for % of Basic components and PF."),
    )
    sequence = models.IntegerField(default=0, verbose_name=_("Order"))

    class Meta:
        ordering = ["sequence", "id"]
        verbose_name = _("Salary Component")
        verbose_name_plural = _("Salary Components")

    def __str__(self) -> str:
        return f"{self.name} ({self.structure_id})"

    def formula_text(self) -> str:
        """Human readable formula label, mirrors the demo UI."""
        if self.component_type == "pct_gross":
            return f"Gross × {self.value}%"
        if self.component_type == "pct_basic":
            return f"Basic × {self.value}%"
        if self.component_type == "flat":
            return _("Flat")
        return _("Gross − other earnings (balancing)")

    def clean(self):
        super().clean()
        if self.component_type == "balance" and self.structure_id_id:
            other_balance = (
                SalaryComponent.objects.filter(
                    structure_id=self.structure_id_id, component_type="balance"
                )
                .exclude(pk=self.pk)
                .exists()
            )
            if other_balance:
                raise ValidationError(
                    _("Only one balancing component is allowed per structure.")
                )


# ---------------------------------------------------------------------------
# 3. Salary Overview — employee ↔ structure assignment (append-only revisions)
# ---------------------------------------------------------------------------
class EmployeeSalary(HorillaModel):
    """
    A salary revision row linking an employee to a salary structure with an
    annual CTC, effective from a date. Rows are append-only: revising pay
    creates a new row, keeping the full salary timeline intact.
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="pro_salary_revisions",
        verbose_name=_("Employee"),
    )
    salary_structure_id = models.ForeignKey(
        SalaryStructure,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name=_("Salary Structure"),
    )
    tax_regime_id = models.ForeignKey(
        TaxRegime,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="salary_assignments",
        verbose_name=_("Tax Regime Override"),
        help_text=_("Leave empty to use the structure's tax regime."),
    )
    annual_ctc = models.FloatField(
        validators=[min_zero], verbose_name=_("Annual CTC")
    )
    effective_date = models.DateField(
        default=date.today, verbose_name=_("Effective Date")
    )
    note = models.CharField(
        max_length=255, blank=True, null=True, verbose_name=_("Note")
    )
    objects = HorillaCompanyManager(EMPLOYEE_COMPANY_FIELD)

    class Meta:
        ordering = ["-effective_date", "-id"]
        verbose_name = _("Employee Salary")
        verbose_name_plural = _("Employee Salaries")

    def __str__(self) -> str:
        return (
            f"{self.employee_id} - {self.salary_structure_id} "
            f"({self.annual_ctc} w.e.f {self.effective_date})"
        )

    @property
    def monthly_ctc(self) -> float:
        return (self.annual_ctc or 0) / 12

    def effective_tax_regime(self):
        """Employee-level override wins over the structure default."""
        return self.tax_regime_id or self.salary_structure_id.tax_regime_id

    @classmethod
    def current_for(cls, employee, on_date=None):
        """
        The revision in force for the employee on ``on_date`` (default today):
        the latest row whose effective_date is on or before that date, or the
        earliest future row as fallback (new joinee assigned ahead of time).
        """
        on_date = on_date or date.today()
        revision = (
            cls.objects.filter(employee_id=employee, effective_date__lte=on_date)
            .order_by("-effective_date", "-id")
            .first()
        )
        if revision is None:
            revision = (
                cls.objects.filter(employee_id=employee)
                .order_by("effective_date", "id")
                .first()
            )
        return revision

    @classmethod
    def current_salaries(cls, on_date=None):
        """Current revision for every employee that has one."""
        # .order_by() clears the model's default ordering first — otherwise
        # Django folds effective_date/id into the SELECT for that ordering,
        # and distinct() stops deduplicating by employee_id alone, letting an
        # employee with multiple revisions appear more than once here.
        employee_ids = (
            cls.objects.order_by().values_list("employee_id", flat=True).distinct()
        )
        revisions = []
        for employee_id in employee_ids:
            revision = cls.current_for(employee_id, on_date)
            if revision:
                revisions.append(revision)
        return revisions


class TaxDeclaration(HorillaModel):
    """
    Per-employee, per-financial-year investment declarations used by regimes
    that allow deductions (Old Regime 80C / 80D style).
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="pro_tax_declarations",
        verbose_name=_("Employee"),
    )
    financial_year_start = models.IntegerField(
        verbose_name=_("Financial Year Start"),
        help_text=_("e.g. 2026 for FY 2026-2027"),
    )
    section_80c = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Section 80C Declared")
    )
    section_80d = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Section 80D Declared")
    )
    proof_submitted = models.BooleanField(
        default=False, verbose_name=_("Proof Submitted")
    )
    objects = HorillaCompanyManager(EMPLOYEE_COMPANY_FIELD)

    class Meta:
        unique_together = ("employee_id", "financial_year_start")
        verbose_name = _("Tax Declaration")
        verbose_name_plural = _("Tax Declarations")

    def __str__(self) -> str:
        return (
            f"{self.employee_id} FY {self.financial_year_start}-"
            f"{self.financial_year_start + 1}"
        )


# ---------------------------------------------------------------------------
# 4. Run Payroll
# ---------------------------------------------------------------------------
class PayrollRun(HorillaModel):
    """
    A monthly payroll run. Draft while the wizard is being worked through;
    completed (locked) after finalize. ``steps_data`` stamps who last touched
    each wizard step and when.
    """

    STATUS_CHOICES = [
        ("draft", _("Draft")),
        ("completed", _("Completed")),
    ]

    year = models.IntegerField(verbose_name=_("Year"))
    month = models.IntegerField(verbose_name=_("Month"))
    total_days = models.FloatField(
        default=0,
        verbose_name=_("Actual Working Days"),
        help_text=_("Working days of the month from the attendance calendar."),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft", verbose_name=_("Status")
    )
    payslips_released = models.BooleanField(
        default=False, verbose_name=_("Payslips Released")
    )
    total_net_pay = models.FloatField(default=0, verbose_name=_("Total Net Pay"))
    steps_data = models.JSONField(default=dict, blank=True)
    company_id = models.ForeignKey(
        Company,
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager()

    class Meta:
        unique_together = ("year", "month", "company_id")
        ordering = ["-year", "-month"]
        verbose_name = _("Payroll Run")
        verbose_name_plural = _("Payroll Runs")

    def __str__(self) -> str:
        return f"Payroll Run {self.month:02d}/{self.year}"

    @property
    def period_start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def period_end(self) -> date:
        import calendar as _calendar

        return date(
            self.year, self.month, _calendar.monthrange(self.year, self.month)[1]
        )

    @property
    def is_locked(self) -> bool:
        return self.status == "completed"


class PayrollRunRecord(HorillaModel):
    """
    One employee's line in a payroll run: editable wizard inputs while the run
    is draft; frozen ``computed`` snapshot + denormalized totals once
    finalized. The snapshot is the immutable source for the payslip.
    """

    PAY_ACTION_CHOICES = [
        ("process", _("Process")),
        ("hold", _("Hold")),
        ("void", _("Skip")),
    ]

    run_id = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name=_("Payroll Run"),
    )
    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="pro_payroll_records",
        verbose_name=_("Employee"),
    )
    salary_revision_id = models.ForeignKey(
        EmployeeSalary,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="run_records",
        verbose_name=_("Salary Revision Used"),
    )
    present_days = models.FloatField(default=0, verbose_name=_("Working Days"))
    lop_days = models.FloatField(default=0, verbose_name=_("LOP Days"))
    bonus = models.FloatField(default=0, validators=[min_zero], verbose_name=_("Bonus"))
    overtime = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Overtime")
    )
    reimbursement = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Reimbursement")
    )
    adhoc_pay = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Adhoc Payment")
    )
    adhoc_deduction = models.FloatField(
        default=0, validators=[min_zero], verbose_name=_("Adhoc Deduction")
    )
    pay_action = models.CharField(
        max_length=10,
        choices=PAY_ACTION_CHOICES,
        default="process",
        verbose_name=_("Pay Action"),
    )
    override_pt = models.FloatField(
        null=True, blank=True, validators=[min_zero], verbose_name=_("PT Override")
    )
    override_tds = models.FloatField(
        null=True, blank=True, validators=[min_zero], verbose_name=_("TDS Override")
    )
    # ----- frozen at finalize -----
    computed = models.JSONField(null=True, blank=True, editable=False)
    gross_earnings = models.FloatField(default=0, verbose_name=_("Gross Earnings"))
    total_deductions = models.FloatField(default=0, verbose_name=_("Total Deductions"))
    net_pay = models.FloatField(default=0, verbose_name=_("Net Pay"))
    sent_to_employee = models.BooleanField(
        default=False, verbose_name=_("Payslip Emailed")
    )
    objects = HorillaCompanyManager(EMPLOYEE_COMPANY_FIELD)

    class Meta:
        unique_together = ("run_id", "employee_id")
        ordering = ["employee_id__employee_first_name"]
        verbose_name = _("Payroll Run Record")
        verbose_name_plural = _("Payroll Run Records")

    def __str__(self) -> str:
        return f"{self.employee_id} - {self.run_id}"

    @property
    def payable_days(self) -> float:
        total = self.run_id.total_days or 0
        return max(0.0, total - (self.lop_days or 0))


# ---------------------------------------------------------------------------
# 5. Automatic run scheduler setting (mirrors PayslipAutoGenerate)
# ---------------------------------------------------------------------------
DAYS = [("last day", _("Last Day"))] + [
    (str(day), f"{day}") for day in range(1, 29)
]


class PayrollProAutoSetting(models.Model):
    """
    Per-company automatic payroll-run configuration. On ``run_day`` of each
    month the scheduler creates the previous month's run, prefills attendance
    and LOP from the attendance / leave tables, computes and (optionally)
    finalizes it and emails the payslips.
    """

    run_day = models.CharField(
        max_length=30,
        choices=DAYS,
        default="1",
        verbose_name=_("Payroll Run Day"),
        help_text=_(
            "On this day of every month the previous month's payroll run "
            "is generated automatically."
        ),
    )
    auto_run = models.BooleanField(default=False, verbose_name=_("Auto Run"))
    auto_finalize = models.BooleanField(
        default=False,
        verbose_name=_("Auto Finalize"),
        help_text=_("Finalize and lock the run automatically after computing."),
    )
    auto_email_payslips = models.BooleanField(
        default=False,
        verbose_name=_("Auto Email Payslips"),
        help_text=_("Release and email payslips right after auto-finalize."),
    )
    company_id = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager(related_company_field="company_id")

    class Meta:
        verbose_name = _("Payroll Auto Run Setting")
        verbose_name_plural = _("Payroll Auto Run Settings")

    def __str__(self) -> str:
        company = self.company_id or _("All companies")
        return f"Auto payroll run ({company})"
