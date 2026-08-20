"""
payroll_pro/views.py

Menus:
1. Tax Regime            (admin)
2. Salary Structures     (admin)
3. Salary Overview       (admin — link employees to structures)
4. Run Payroll           (admin — 6 step monthly wizard)
5. Payslips & My Finance (admin payslips + employee self service)
"""

import calendar
import json
import logging
from datetime import date, datetime

from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from base.models import Company
from employee.models import Employee
from horilla.decorators import login_required, permission_required
from horilla.horilla_middlewares import get_selected_company
from horilla.methods import handle_no_permission
from payroll.models.models import Reimbursement
from payroll_pro import services
from payroll_pro.forms import (
    BulkEmployeeSalaryForm,
    EmployeeSalaryForm,
    PayrollProAutoSettingForm,
    ReimbursementRequestForm,
    SalaryComponentForm,
    SalaryStructureForm,
    TaxDeclarationForm,
    TaxRegimeForm,
)
from payroll_pro.models import (
    EmployeeSalary,
    PayrollProAutoSetting,
    PayrollRun,
    PayrollRunRecord,
    SalaryComponent,
    SalaryStructure,
    TaxDeclaration,
    TaxRegime,
    TaxSlab,
)

logger = logging.getLogger(__name__)

MONTH_NAMES = [calendar.month_name[i] for i in range(1, 13)]

RUN_STEPS = [
    {
        "id": "attendance",
        "icon": "calendar-outline",
        "cls": "c1",
        "title": _("Leave, Attendance & Payable Units"),
        "sub": _("LOP summary, adjustments & payable days"),
    },
    {
        "id": "joinees",
        "icon": "person-add-outline",
        "cls": "c2",
        "title": _("New Joinees & Exits"),
        "sub": _("Pay actions for mid-cycle joinees and exits"),
    },
    {
        "id": "bonus",
        "icon": "briefcase-outline",
        "cls": "c3",
        "title": _("Bonus, Salary Revisions & Overtime"),
        "sub": _("One-time payouts effective this cycle"),
    },
    {
        "id": "adhoc",
        "icon": "receipt-outline",
        "cls": "c4",
        "title": _("Reimbursement, Adhoc Payment, Deduction"),
        "sub": _("Claims, additions & one-off deductions"),
    },
    {
        "id": "holds",
        "icon": "hourglass-outline",
        "cls": "c5",
        "title": _("Salaries on Hold"),
        "sub": _("Hold / void / release salary processing"),
    },
    {
        "id": "overrides",
        "icon": "options-outline",
        "cls": "c6",
        "title": _("Override (PT, TDS)"),
        "sub": _("Manual statutory overrides for this month"),
    },
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _stamp(run, step_id, user):
    """Record who last touched a wizard step and when."""
    steps = run.steps_data or {}
    try:
        by = user.employee_get.get_full_name()
    except Exception:
        by = user.get_username()
    steps[step_id] = {"at": datetime.now().strftime("%d %b %Y %I:%M %p"), "by": by}
    run.steps_data = steps
    run.save(update_fields=["steps_data"])


def _current_run_company():
    """
    The company a newly-created PayrollRun should be stamped with: the
    request's currently selected company, falling back to the HQ company
    when none is selected (e.g. the acting admin has "All companies"
    picked). Without this, PayrollRun.company_id stayed null on every run,
    and HorillaCompanyManager treats a null company_id as visible to every
    company - so every company ended up sharing the exact same PayrollRun
    row (and its lock/finalize/total_net_pay) for a given month.
    """
    company = get_selected_company()
    if company and company != "all":
        # Session-sourced, so always a numeric string - normalize to int to
        # match Company.pk (a bare string here would still round-trip
        # through the DB fine via get_prep_value, but leaves the in-memory
        # PayrollRun.company_id_id typed inconsistently with every other
        # company FK id in the app until the row is reloaded).
        try:
            return int(company)
        except (TypeError, ValueError):
            pass
    default = Company.objects.filter(hq=True).first() or Company.objects.first()
    return default.pk if default else None


def ensure_run(year, month):
    """
    Get or create the month's run and make sure every employee with a salary
    assignment has a record, prefilled with attendance / LOP from the
    attendance and leave tables. Locked runs are returned untouched.
    """
    run = PayrollRun.objects.filter(year=year, month=month).first()
    if run is None:
        run = PayrollRun(
            year=year,
            month=month,
            company_id_id=_current_run_company(),
            total_days=services.get_month_working_days(year, month),
        )
        run.save()
    if run.is_locked:
        return run
    existing_ids = set(run.records.values_list("employee_id", flat=True))
    for revision in EmployeeSalary.current_salaries(on_date=run.period_end):
        if revision.employee_id_id in existing_ids:
            continue
        if not revision.employee_id.is_active:
            continue
        attendance = services.get_attendance_and_lop(
            revision.employee_id, year, month
        )
        PayrollRunRecord.objects.create(
            run_id=run,
            employee_id=revision.employee_id,
            salary_revision_id=revision,
            present_days=attendance["present_days"],
            lop_days=attendance["lop_days"],
        )
    return run


def _records_with_calc(run):
    """[(record, computed_dict_or_None), ...] for the run."""
    rows = []
    for record in run.records.select_related(
        "employee_id", "salary_revision_id", "salary_revision_id__salary_structure_id"
    ):
        calc = None
        if record.pay_action == "process":
            try:
                calc = services.compute_for_record(record)
            except Exception:
                logger.exception("Computation failed for %s", record)
        rows.append((record, calc))
    return rows


def _exiting_employees(records, run):
    """
    [(record, OffboardingEmployee), ...] for run records whose employee's
    notice period has actually started (not archived, and not merely added
    to Offboarding tracking with no notice period started yet), wired to the
    real offboarding module rather than a placeholder.
    """
    from offboarding.models import OffboardingEmployee

    records_by_employee = {record.employee_id_id: record for record in records}
    if not records_by_employee:
        return []
    offboarding_qs = (
        OffboardingEmployee.objects.exclude(stage_id__type="archived")
        .filter(
            employee_id_id__in=records_by_employee.keys(),
            notice_period_starts__isnull=False,
            notice_period_starts__lte=run.period_end,
        )
        .select_related("employee_id", "stage_id")
    )
    return [
        (records_by_employee[ob.employee_id_id], ob) for ob in offboarding_qs
    ]


def _tracked_overtime_hours(records, run):
    """
    {employee_id: hours} of attendance-tracked overtime (Attendance.
    overtime_second) within the run's period — shown as reference alongside
    the manual overtime amount input, linking it to actual attendance data.
    """
    from attendance.models import Attendance

    employee_ids = [record.employee_id_id for record in records]
    if not employee_ids:
        return {}
    rows = (
        Attendance.objects.filter(
            employee_id_id__in=employee_ids,
            attendance_date__range=(run.period_start, run.period_end),
            attendance_validated=True,
        )
        .values("employee_id_id")
        .annotate(total_seconds=Sum("overtime_second"))
    )
    return {
        row["employee_id_id"]: round((row["total_seconds"] or 0) / 3600, 2)
        for row in rows
    }


def _month_strip(year, month):
    """Financial-year月 chips (April..March) around the selected month."""
    fy = year if month >= 4 else year - 1
    today = date.today()
    runs = {
        (r.year, r.month): r
        for r in PayrollRun.objects.filter(year__in=[fy, fy + 1])
    }
    chips = []
    for i in range(12):
        m = ((3 + i) % 12) + 1
        y = fy if m >= 4 else fy + 1
        run = runs.get((y, m))
        if run and run.status == "completed":
            status, cls = _("Completed"), "completed"
        elif (y, m) == (today.year, today.month):
            status, cls = _("Current"), "current"
        elif date(y, m, 1) < date(today.year, today.month, 1):
            status, cls = _("Pending"), ""
        else:
            status, cls = _("Upcoming"), ""
        chips.append(
            {
                "year": y,
                "month": m,
                "label": f"{MONTH_NAMES[m - 1][:3]} {str(y)[2:]}",
                "status": status,
                "cls": cls,
                "selected": (y, m) == (year, month),
            }
        )
    return {"fy": fy, "chips": chips}


def _own_employee(request):
    try:
        return request.user.employee_get
    except Exception:
        return None


# ===========================================================================
# 1. TAX REGIME
# ===========================================================================
@login_required
@permission_required("payroll_pro.view_taxregime")
def tax_regime_view(request):
    """Tax regime cards; create/edit happens on a dedicated full page."""
    context = {"regimes": TaxRegime.objects.prefetch_related("slabs")}
    return render(request, "payroll_pro/tax_regime.html", context)


@login_required
def tax_regime_form(request, pk=None):
    """Full-page create/edit form for a tax regime (replaces the offcanvas —
    a real page navigation, instead of a mid-panel reload)."""
    required_perm = "payroll_pro.change_taxregime" if pk else "payroll_pro.add_taxregime"
    if not request.user.has_perm(required_perm):
        return handle_no_permission(request)
    instance = get_object_or_404(TaxRegime, pk=pk) if pk else None
    locked = bool(instance and instance.is_locked)
    context = {
        "form": TaxRegimeForm(instance=instance, prefix="form", locked=locked),
        "edit_instance": instance,
        "locked": locked,
    }
    return render(request, "payroll_pro/tax_regime_form.html", context)


def _regime_dependents(regime):
    """Structures linked to this regime and the true employee count on it
    (via effective_tax_regime(), not just a bare structure count)."""
    structures = list(regime.salary_structures.all())
    return {
        "structures": structures,
        "employee_count": regime.current_employee_count(),
    }


@login_required
@permission_required("payroll_pro.view_taxregime")
def tax_regime_detail_modal(request, pk):
    """Centered-modal "View" — full regime detail, including the actual
    linked structures/employees the card itself doesn't show."""
    regime = get_object_or_404(TaxRegime.objects.prefetch_related("slabs"), pk=pk)
    context = {"regime": regime, "dependents": _regime_dependents(regime)}
    return render(request, "payroll_pro/_modal_regime_detail.html", context)


@login_required
@permission_required("payroll_pro.delete_taxregime")
def tax_regime_delete_confirm(request, pk):
    """Centered-modal delete confirmation — summarizes dependents instead of
    a bare browser confirm(), and never offers a delete action when blocked."""
    regime = get_object_or_404(TaxRegime, pk=pk)
    context = {"regime": regime, "dependents": _regime_dependents(regime)}
    return render(request, "payroll_pro/_modal_delete_regime.html", context)


def _parse_slab_rows(request, prefix):
    """Collect non-empty slab rows from the modal's slab array inputs."""
    mins = request.POST.getlist(f"{prefix}-slab_min")
    maxes = request.POST.getlist(f"{prefix}-slab_max")
    rates = request.POST.getlist(f"{prefix}-slab_rate")
    rows = []
    for min_value, max_value, rate in zip(mins, maxes, rates):
        if min_value == "" and max_value == "" and rate == "":
            continue
        rows.append((min_value, max_value, rate))
    return rows


def _validate_slabs(rows):
    """Validate slab rows before they're persisted. Returns an error message
    (str) if invalid, or None if the rows are safe to save."""
    if not rows:
        return _("At least one tax slab is required.")

    parsed = []
    for index, (min_value, max_value, rate) in enumerate(rows, start=1):
        if min_value == "" or rate == "":
            return _(
                "Slab %(row)d: 'From' and 'Rate %%' are required."
            ) % {"row": index}
        min_amount = _float(min_value, None)
        rate_pct = _float(rate, None)
        if min_amount is None or min_amount < 0:
            return _(
                "Slab %(row)d: 'From' must be a non-negative number."
            ) % {"row": index}
        if rate_pct is None or rate_pct < 0 or rate_pct > 100:
            return _(
                "Slab %(row)d: 'Rate %%' must be between 0 and 100."
            ) % {"row": index}
        max_amount = None
        if max_value != "":
            max_amount = _float_or_none(max_value)
            if max_amount is None:
                return _(
                    "Slab %(row)d: 'To' must be a non-negative number."
                ) % {"row": index}
            if max_amount <= min_amount:
                return _(
                    "Slab %(row)d: 'To' must be greater than 'From'."
                ) % {"row": index}
        parsed.append((min_amount, max_amount))

    parsed.sort(key=lambda item: item[0])
    if parsed[0][0] != 0:
        return _("The first tax slab must start from 0.")
    for index in range(1, len(parsed)):
        prev_max = parsed[index - 1][1]
        if prev_max is None:
            return _("Only the last tax slab can be left open-ended ('To' empty).")
        if parsed[index][0] != prev_max:
            return _(
                "Tax slabs must be contiguous — the next slab should start "
                "at %(prev)s, where the previous one ends."
            ) % {"prev": prev_max}
    return None


def _save_slabs(regime, rows):
    """Rebuild the regime's slab rows from already-validated slab rows."""
    regime.slabs.all().delete()
    for min_value, max_value, rate in rows:
        TaxSlab.objects.create(
            tax_regime_id=regime,
            min_amount=_float(min_value),
            max_amount=_float_or_none(max_value),
            rate_pct=_float(rate),
        )


@login_required
@permission_required("payroll_pro.add_taxregime")
def tax_regime_save(request):
    """Create / update a regime together with its slab rows."""
    if request.method != "POST":
        return redirect("pp-tax-regimes")
    pk = request.POST.get("pk")
    instance = TaxRegime.objects.filter(pk=pk).first() if pk else None
    locked = bool(instance and instance.is_locked)
    form = TaxRegimeForm(request.POST, instance=instance, prefix="form", locked=locked)

    slab_rows = _parse_slab_rows(request, "form") if not locked else []
    slab_error = _validate_slabs(slab_rows) if not locked else None

    if form.is_valid() and not slab_error:
        with transaction.atomic():
            regime = form.save()
            if not locked:
                _save_slabs(regime, slab_rows)
        messages.success(request, _("Tax regime saved."))
        return redirect("pp-tax-regimes")

    if not form.is_valid():
        messages.error(request, form.errors.as_text())
    elif slab_error:
        messages.error(request, slab_error)
    if pk:
        return redirect("pp-tax-regime-edit", pk=pk)
    return redirect("pp-tax-regime-new")


@login_required
@permission_required("payroll_pro.delete_taxregime")
def tax_regime_delete(request, pk):
    regime = get_object_or_404(TaxRegime, pk=pk)
    if regime.salary_structures.exists() or regime.salary_assignments.exists():
        messages.error(
            request,
            _("This regime is linked to salary structures or employees and "
              "cannot be deleted."),
        )
    else:
        regime.delete()
        messages.success(request, _("Tax regime deleted."))
    return redirect("pp-tax-regimes")


@login_required
@permission_required("payroll_pro.add_taxregime")
def tax_regime_duplicate(request, pk):
    """Clone a regime (with its slabs) so a locked, currently-in-use regime
    can still be revised via an editable copy."""
    if request.method != "POST":
        return redirect("pp-tax-regimes")
    original = get_object_or_404(TaxRegime, pk=pk)
    original_slabs = list(original.ordered_slabs())
    duplicate = TaxRegime.objects.get(pk=pk)
    duplicate.pk = None
    duplicate.id = None
    duplicate._state.adding = True
    duplicate.name = _("%(name)s (Copy)") % {"name": original.name}
    duplicate.save()
    for slab in original_slabs:
        slab.pk = None
        slab.id = None
        slab._state.adding = True
        slab.tax_regime_id = duplicate
        slab.save()
    messages.success(
        request,
        _('Created "%(name)s" as an editable copy.') % {"name": duplicate.name},
    )
    return redirect("pp-tax-regime-edit", pk=duplicate.pk)


# ===========================================================================
# 2. SALARY STRUCTURES
# ===========================================================================
DEFAULT_COMPONENTS = [
    ("Basic", "pct_gross", 40, True),
    ("House Rent Allowance", "pct_basic", 50, False),
    ("Conveyance Allowance", "flat", 1600, False),
    ("Medical Allowance", "flat", 1250, False),
    ("Special Allowance", "balance", 0, False),
]


def _structure_employees(structure):
    """Current salary revisions whose structure is this one (with employees)."""
    return [
        revision
        for revision in EmployeeSalary.current_salaries()
        if revision.salary_structure_id_id == structure.pk
    ]


@login_required
@permission_required("payroll_pro.view_salarystructure")
def structure_view(request):
    """Structure cards with avatars & kebab actions. Create/edit happens on
    a dedicated full page."""
    structures = SalaryStructure.objects.prefetch_related(
        "components"
    ).select_related("tax_regime_id")
    cards = [
        {"structure": structure, "employees": _structure_employees(structure)}
        for structure in structures
    ]
    context = {"cards": cards}
    return render(request, "payroll_pro/structures.html", context)


@login_required
def structure_form(request, pk=None):
    """Full-page create/edit form for a salary structure (replaces the
    offcanvas — a real page navigation, instead of a mid-panel reload)."""
    required_perm = (
        "payroll_pro.change_salarystructure" if pk else "payroll_pro.add_salarystructure"
    )
    if not request.user.has_perm(required_perm):
        return handle_no_permission(request)
    instance = get_object_or_404(SalaryStructure, pk=pk) if pk else None
    locked = bool(instance and instance.is_locked)
    create_initial = {
        "gross_mode": "pct",
        "gross_pct": 100,
        "pf_enabled": True,
        "pf_employee_pct": 12,
        "pf_employer_pct": 12,
        "pf_wage_ceiling": 15000,
        "pt_enabled": True,
        "pt_threshold": 15000,
        "pt_amount": 200,
        "gratuity_enabled": True,
        "tds_enabled": True,
    }
    context = {
        "form": SalaryStructureForm(
            instance=instance,
            prefix="form",
            initial=None if instance else create_initial,
            locked=locked,
        ),
        "edit_instance": instance,
        "locked": locked,
    }
    return render(request, "payroll_pro/structure_form.html", context)


@login_required
@permission_required("payroll_pro.delete_salarystructure")
def structure_delete_confirm(request, pk):
    """Centered-modal delete confirmation — summarizes assigned employees
    instead of a bare browser confirm()."""
    structure = get_object_or_404(SalaryStructure, pk=pk)
    context = {"structure": structure, "employees": _structure_employees(structure)}
    return render(request, "payroll_pro/_modal_delete_structure.html", context)


@login_required
@permission_required("payroll_pro.view_salarystructure")
def structure_detail(request, pk):
    """Full structure page: settings form, component editor, formula logic."""
    structure = get_object_or_404(
        SalaryStructure.objects.prefetch_related("components"), pk=pk
    )
    context = {
        "structure": structure,
        "form": SalaryStructureForm(instance=structure, prefix="form"),
        "employees": _structure_employees(structure),
        "component_types": SalaryComponent.COMPONENT_TYPE_CHOICES,
    }
    return render(request, "payroll_pro/structure_detail.html", context)


@login_required
@permission_required("payroll_pro.add_salarystructure")
def structure_create(request):
    """Create a structure (modal form), pre-seeded with standard components."""
    if request.method != "POST":
        return redirect("pp-structures")
    form = SalaryStructureForm(request.POST, prefix="form")
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect("pp-structure-new")
    structure = form.save()
    for sequence, (comp_name, comp_type, value, is_basic) in enumerate(
        DEFAULT_COMPONENTS
    ):
        SalaryComponent.objects.create(
            structure_id=structure,
            name=comp_name,
            component_type=comp_type,
            value=value,
            is_basic=is_basic,
            sequence=sequence,
        )
    messages.success(
        request,
        _("Salary structure created with standard components — open it to "
          "fine-tune the breakup."),
    )
    return redirect("pp-structure-detail", pk=structure.pk)


@login_required
@permission_required("payroll_pro.change_salarystructure")
def structure_update(request, pk):
    structure = get_object_or_404(SalaryStructure, pk=pk)
    locked = structure.is_locked
    if request.method == "POST":
        form = SalaryStructureForm(
            request.POST, instance=structure, prefix="form", locked=locked
        )
        if form.is_valid():
            form.save()
            messages.success(request, _("Salary structure updated."))
            return redirect("pp-structure-detail", pk=structure.pk)
        messages.error(request, form.errors.as_text())
        return redirect("pp-structure-edit", pk=structure.pk)
    return redirect("pp-structure-detail", pk=structure.pk)


@login_required
@permission_required("payroll_pro.add_salarystructure")
def structure_duplicate(request, pk):
    """Clone a structure (with its components) so a locked, currently-in-use
    structure can still be revised via an editable copy."""
    if request.method != "POST":
        return redirect("pp-structures")
    original = get_object_or_404(SalaryStructure, pk=pk)
    original_components = list(original.ordered_components())
    duplicate = SalaryStructure.objects.get(pk=pk)
    duplicate.pk = None
    duplicate.id = None
    duplicate._state.adding = True
    duplicate.name = _("%(name)s (Copy)") % {"name": original.name}
    duplicate.save()
    for component in original_components:
        component.pk = None
        component.id = None
        component._state.adding = True
        component.structure_id = duplicate
        component.save()
    messages.success(
        request,
        _('Created "%(name)s" as an editable copy.') % {"name": duplicate.name},
    )
    return redirect("pp-structure-edit", pk=duplicate.pk)


@login_required
@permission_required("payroll_pro.delete_salarystructure")
def structure_delete(request, pk):
    structure = get_object_or_404(SalaryStructure, pk=pk)
    if structure.assignments.exists():
        messages.error(
            request,
            _("Employees are assigned to this structure. Reassign them before "
              "deleting — historical payslips are unaffected either way."),
        )
    else:
        structure.delete()
        messages.success(request, _("Salary structure deleted."))
    return redirect("pp-structures")


def _is_htmx(request):
    return bool(request.headers.get("HX-Request"))


def _render_components_partial(request, structure, error=None):
    """The 'Earning Components' card, re-rendered for an htmx swap after
    add/update/delete — keeps the component editor in sync with the database
    without a full page reload."""
    return render(
        request,
        "payroll_pro/_structure_components.html",
        {
            "structure": structure,
            "component_types": SalaryComponent.COMPONENT_TYPE_CHOICES,
            "component_error": error,
        },
    )


@login_required
@permission_required("payroll_pro.change_salarystructure")
def component_add(request, structure_pk):
    structure = get_object_or_404(SalaryStructure, pk=structure_pk)
    htmx = _is_htmx(request)
    if structure.is_locked:
        error = _(
            "This structure has employees currently assigned and can't be "
            "edited. Duplicate it to make changes."
        )
        if htmx:
            return _render_components_partial(request, structure, error=error)
        messages.error(request, error)
        return redirect("pp-structure-detail", pk=structure.pk)
    if request.method == "POST":
        form = SalaryComponentForm(request.POST)
        if form.is_valid():
            component = form.save(commit=False)
            component.structure_id = structure
            component.sequence = structure.components.count()
            try:
                component.full_clean()
                component.save()
                if not htmx:
                    messages.success(request, _("Component added."))
            except Exception as error:
                if htmx:
                    return _render_components_partial(
                        request, structure, error=str(error)
                    )
                messages.error(request, str(error))
        elif htmx:
            return _render_components_partial(
                request, structure, error=form.errors.as_text()
            )
        else:
            messages.error(request, form.errors.as_text())
    if htmx:
        return _render_components_partial(request, structure)
    return redirect("pp-structure-detail", pk=structure.pk)


@login_required
@permission_required("payroll_pro.change_salarystructure")
def component_update(request, pk):
    component = get_object_or_404(SalaryComponent, pk=pk)
    structure = component.structure_id
    htmx = _is_htmx(request)
    if structure.is_locked:
        error = _(
            "This structure has employees currently assigned and can't be "
            "edited. Duplicate it to make changes."
        )
        if htmx:
            return _render_components_partial(request, structure, error=error)
        messages.error(request, error)
        return redirect("pp-structure-detail", pk=structure.pk)
    if request.method == "POST":
        component.name = request.POST.get("name", component.name)
        component.component_type = request.POST.get(
            "component_type", component.component_type
        )
        component.value = _float(request.POST.get("value"), component.value)
        try:
            component.full_clean()
            component.save()
        except Exception as error:
            if htmx:
                return _render_components_partial(
                    request, structure, error=str(error)
                )
            messages.error(request, str(error))
    if htmx:
        return _render_components_partial(request, structure)
    return redirect("pp-structure-detail", pk=structure.pk)


@login_required
@permission_required("payroll_pro.change_salarystructure")
def component_delete(request, pk):
    """
    Any component — including Basic and the balancing component — can be
    removed. A structure with no components at all still pays out correctly:
    services.compute_components() falls back to a single "Gross Pay" line
    for the derived gross (e.g. a consultant structure that only needs
    PT/TDS, with no Basic/HRA breakup).
    """
    component = get_object_or_404(SalaryComponent, pk=pk)
    structure = component.structure_id
    htmx = _is_htmx(request)
    if structure.is_locked:
        error = _(
            "This structure has employees currently assigned and can't be "
            "edited. Duplicate it to make changes."
        )
        if htmx:
            return _render_components_partial(request, structure, error=error)
        messages.error(request, error)
        return redirect("pp-structure-detail", pk=structure.pk)
    component.delete()
    if htmx:
        return _render_components_partial(request, structure)
    messages.success(request, _("Component removed."))
    return redirect("pp-structure-detail", pk=structure.pk)


# ===========================================================================
# 3. SALARY OVERVIEW
# ===========================================================================
@login_required
@permission_required("payroll_pro.view_employeesalary")
def salary_overview_view(request):
    """
    Employees with (or without) a salary assignment; bulk assign/revise
    panel (multi-select — one employee or many at once).
    """
    current = {
        revision.employee_id_id: revision
        for revision in EmployeeSalary.current_salaries()
    }
    rows = []
    for employee in Employee.objects.filter(is_active=True):
        rows.append({"employee": employee, "revision": current.get(employee.pk)})
    assigned = [row for row in rows if row["revision"]]
    unassigned = [row for row in rows if not row["revision"]]
    show_canvas = bool(request.GET.get("assign"))
    context = {
        "assigned": assigned,
        "unassigned": unassigned,
        "form": BulkEmployeeSalaryForm(),
        "show_canvas": show_canvas,
    }
    return render(request, "payroll_pro/overview.html", context)


def _employee_missing_statutory_details(employee):
    """
    PAN/UAN live on the employee's Bank Details record (EmployeeBankDetails),
    not on Employee directly — and that related record may not exist yet.
    ``employee_bank_details`` is Django's reverse-OneToOne accessor: missing
    it raises RelatedObjectDoesNotExist, which is also an AttributeError, so
    getattr(..., None) resolves it safely.
    """
    bank_details = getattr(employee, "employee_bank_details", None)
    return (
        not bank_details
        or not bank_details.pan_number
        or not bank_details.uan_number
    )


@login_required
@permission_required("payroll_pro.add_employeesalary")
def salary_assign(request):
    """Create a new salary revision row for every selected employee."""
    if request.method != "POST":
        return redirect("pp-salary-overview")
    form = BulkEmployeeSalaryForm(request.POST)
    if form.is_valid():
        missing = [
            employee
            for employee in form.cleaned_data["employee_ids"]
            if _employee_missing_statutory_details(employee)
        ]
        if missing:
            names = ", ".join(employee.get_full_name() for employee in missing)
            messages.error(
                request,
                _(
                    "Couldn't save — PAN/UAN not on file for: %(names)s. "
                    "Add these details in their Employee profile (Bank & "
                    "Statutory Details) first, then try again."
                )
                % {"names": names},
            )
            return redirect(f"{reverse('pp-salary-overview')}?assign=1")
        created = form.save()
        messages.success(
            request,
            _("Salary revision recorded for %(count)d employee(s).")
            % {"count": len(created)},
        )
        return redirect("pp-salary-overview")
    messages.error(request, form.errors.as_text())
    return redirect(f"{reverse('pp-salary-overview')}?assign=1")


@login_required
@permission_required("payroll_pro.view_employeesalary")
def salary_timeline(request, employee_pk):
    """Full salary timeline + current breakup for one employee."""
    employee = get_object_or_404(Employee, pk=employee_pk)
    revisions = EmployeeSalary.objects.filter(employee_id=employee)
    current = EmployeeSalary.current_for(employee)
    breakup = None
    if current:
        breakup = services.compute_salary(
            current, {"present_days": 30, "lop_days": 0, "total_days": 30}
        )
    context = {
        "employee": employee,
        "revisions": revisions,
        "current": current,
        "breakup": breakup,
        "form": EmployeeSalaryForm(
            initial={
                "employee_id": employee,
                "salary_structure_id": current.salary_structure_id if current else None,
                "annual_ctc": current.annual_ctc if current else None,
            }
        ),
    }
    return render(request, "payroll_pro/timeline.html", context)


@login_required
@permission_required("payroll_pro.add_employeesalary")
def revision_add(request, employee_pk):
    """Record a single pay revision for one employee (used by both the
    per-employee timeline and the standalone Pay Revisions menu)."""
    employee = get_object_or_404(Employee, pk=employee_pk)
    if request.method == "POST":
        if _employee_missing_statutory_details(employee):
            messages.error(
                request,
                _(
                    "Couldn't save — PAN/UAN not on file for %(name)s. Add "
                    "these details in their Employee profile (Bank & "
                    "Statutory Details) first, then try again."
                )
                % {"name": employee.get_full_name()},
            )
        else:
            form = EmployeeSalaryForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, _("Pay revision recorded."))
            else:
                messages.error(request, form.errors.as_text())
    if request.POST.get("next") == "revisions":
        return redirect(f"{reverse('pp-pay-revisions')}?employee={employee.pk}")
    return redirect("pp-salary-timeline", employee_pk=employee.pk)


# ===========================================================================
# 3b. PAY REVISIONS — standalone menu, bifurcated from Salary Overview
# ===========================================================================
@login_required
@permission_required("payroll_pro.view_employeesalary")
def pay_revisions_view(request):
    """
    Employee picker + add-revision form + history, as its own top-level
    menu separate from Salary Overview's initial-assignment flow.
    """
    # Only employees already on payroll (at least one salary revision) —
    # a first-time assignment belongs in Salary Overview's Assign / Revise
    # Salary flow, not here.
    employees = Employee.objects.filter(
        is_active=True, pro_salary_revisions__isnull=False
    ).distinct()
    employee_pk = request.GET.get("employee")
    employee = employees.filter(pk=employee_pk).first() if employee_pk else None
    if not employee:
        first_assigned = EmployeeSalary.current_salaries()
        if first_assigned:
            employee = first_assigned[0].employee_id
        else:
            employee = employees.first()
    if not employee:
        return render(
            request,
            "payroll_pro/pay_revisions.html",
            {"employees": employees, "employee": None},
        )

    revisions = EmployeeSalary.objects.filter(employee_id=employee)
    current = EmployeeSalary.current_for(employee)
    context = {
        "employees": employees,
        "employee": employee,
        "revisions": revisions,
        "current": current,
        "show_picker": True,
        "form": EmployeeSalaryForm(
            initial={
                "employee_id": employee,
                "salary_structure_id": current.salary_structure_id if current else None,
                "annual_ctc": current.annual_ctc if current else None,
            }
        ),
    }
    return render(request, "payroll_pro/pay_revisions.html", context)


# ===========================================================================
# 4. RUN PAYROLL
# ===========================================================================
@login_required
@permission_required("payroll_pro.view_payrollrun")
def run_payroll_view(request):
    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))
    step = request.GET.get("step", "tiles")
    run = ensure_run(year, month)

    context = {
        "run": run,
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month - 1],
        "strip": _month_strip(year, month),
        "steps": RUN_STEPS,
        "steps_data": run.steps_data or {},
        "step": step,
    }

    if step == "tiles":
        context["record_count"] = run.records.count()
        return render(request, "payroll_pro/run/tiles.html", context)

    if step == "review":
        rows = _records_with_calc(run)
        active = [(r, c) for r, c in rows if r.pay_action == "process" and c]
        excluded = [r for r, _c in rows if r.pay_action != "process"]
        totals = {
            "gross": sum(c["gross_earnings"] for _r, c in active),
            "deductions": sum(c["total_deductions"] for _r, c in active),
            "net": sum(c["net_pay"] for _r, c in active),
            "cost": sum(c["total_employer_cost"] for _r, c in active),
        }
        context.update({"rows": active, "excluded": excluded, "totals": totals})
        return render(request, "payroll_pro/run/review.html", context)

    step_meta = next((s for s in RUN_STEPS if s["id"] == step), None)
    if not step_meta:
        raise Http404
    context["step_meta"] = step_meta
    records = run.records.select_related(
        "employee_id", "salary_revision_id", "salary_revision_id__salary_structure_id"
    )
    context["records"] = records

    if step == "joinees":
        start, end = run.period_start, run.period_end
        joinees = []
        for record in records:
            info = getattr(record.employee_id, "employee_work_info", None)
            joining = getattr(info, "date_joining", None)
            if joining and start <= joining <= end:
                joinees.append((record, joining))
        context["joinees"] = joinees
        context["exiting"] = _exiting_employees(records, run)
    elif step == "bonus":
        context["revisions"] = EmployeeSalary.objects.filter(
            effective_date__range=(run.period_start, run.period_end)
        )
        context["overtime_hours"] = _tracked_overtime_hours(records, run)
    elif step == "adhoc":
        employee_ids = [record.employee_id_id for record in records]
        context["pending_claims"] = Reimbursement.objects.filter(
            employee_id_id__in=employee_ids, type="reimbursement", status="requested"
        ).select_related("employee_id")
    elif step == "overrides":
        context["override_rows"] = [
            (record, services.compute_for_record(record))
            for record in records
            if record.pay_action == "process"
        ]
    return render(request, f"payroll_pro/run/step_{step}.html", context)


@login_required
@permission_required("payroll_pro.view_payrollrun")
def run_record_detail_modal(request, run_pk, record_pk):
    """Centered-modal calculation breakdown for one Review & Finalize row —
    replaces the fragile CSS accordion."""
    run = get_object_or_404(PayrollRun, pk=run_pk)
    record = get_object_or_404(
        run.records.select_related("employee_id"), pk=record_pk
    )
    calc = services.compute_for_record(record)
    if calc is None:
        raise Http404
    context = {"record": record, "calc": calc, "run": run}
    return render(request, "payroll_pro/run/_modal_record_detail.html", context)


def _save_claim_preserving_employee(claim):
    """
    payroll.Reimbursement.save() reassigns employee_id to the acting user
    whenever that user lacks payroll.change_reimbursement — fine for the
    employee's own self-service submission, but wrong here where a
    payroll_pro admin (gated on a different permission) is approving someone
    else's claim. Guard against that silent reassignment.
    """
    original_employee_id = claim.employee_id_id
    claim.save()
    if claim.employee_id_id != original_employee_id:
        claim.employee_id_id = original_employee_id
        claim.save()


@login_required
@permission_required("payroll_pro.change_payrollrun")
def claim_approve(request, run_pk, claim_pk):
    """Approve a pending reimbursement claim and add it to that employee's
    reimbursement amount for this run."""
    run = get_object_or_404(PayrollRun, pk=run_pk)
    claim = get_object_or_404(Reimbursement, pk=claim_pk, type="reimbursement")
    if request.method == "POST" and not run.is_locked:
        record = run.records.filter(employee_id=claim.employee_id).first()
        if record:
            claim.status = "approved"
            _save_claim_preserving_employee(claim)
            record.reimbursement = (record.reimbursement or 0) + claim.amount
            record.save(update_fields=["reimbursement"])
            _stamp(run, "adhoc", request.user)
            messages.success(request, _("Claim approved and added to this month's pay."))
        else:
            messages.error(request, _("This employee is not part of this payroll run."))
    return redirect(
        f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}&step=adhoc"
    )


@login_required
@permission_required("payroll_pro.change_payrollrun")
def claim_reject(request, run_pk, claim_pk):
    """Reject a pending reimbursement claim."""
    run = get_object_or_404(PayrollRun, pk=run_pk)
    claim = get_object_or_404(Reimbursement, pk=claim_pk, type="reimbursement")
    if request.method == "POST" and not run.is_locked:
        claim.status = "rejected"
        _save_claim_preserving_employee(claim)
        messages.success(request, _("Claim rejected."))
    return redirect(
        f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}&step=adhoc"
    )


@login_required
@permission_required("payroll_pro.change_payrollrun")
def run_step_save(request):
    """Bulk-save a wizard step's inputs for every record, then stamp it."""
    if request.method != "POST":
        return redirect("pp-run-payroll")
    run = get_object_or_404(PayrollRun, pk=request.POST.get("run_id"))
    step = request.POST.get("step", "tiles")
    if run.is_locked:
        messages.error(request, _("This payroll run is locked."))
        return redirect(f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}")

    for record in run.records.all():
        prefix = f"r{record.pk}_"
        changed = False
        field_map = {
            "bonus": "bonus",
            "overtime": "overtime",
            "reimbursement": "reimbursement",
            "adhoc_pay": "adhoc_pay",
            "adhoc_deduction": "adhoc_deduction",
        }
        if f"{prefix}present" in request.POST:
            present = max(
                0.0, min(run.total_days, _float(request.POST[f"{prefix}present"]))
            )
            record.present_days = present
            record.lop_days = run.total_days - present
            changed = True
        if f"{prefix}lop" in request.POST:
            lop = max(0.0, min(run.total_days, _float(request.POST[f"{prefix}lop"])))
            record.lop_days = lop
            record.present_days = run.total_days - lop
            changed = True
        for post_field, model_field in field_map.items():
            key = f"{prefix}{post_field}"
            if key in request.POST:
                setattr(record, model_field, max(0.0, _float(request.POST[key])))
                changed = True
        if f"{prefix}pay_action" in request.POST:
            action = request.POST[f"{prefix}pay_action"]
            if action in ("process", "hold", "void"):
                record.pay_action = action
                changed = True
        if f"{prefix}override_pt" in request.POST:
            record.override_pt = _float_or_none(request.POST[f"{prefix}override_pt"])
            changed = True
        if f"{prefix}override_tds" in request.POST:
            record.override_tds = _float_or_none(request.POST[f"{prefix}override_tds"])
            changed = True
        if changed:
            record.save()

    if step in [s["id"] for s in RUN_STEPS]:
        _stamp(run, step, request.user)
    messages.success(request, _("Saved."))
    target = request.POST.get("next", "tiles")
    return redirect(
        f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}&step={target}"
    )


@login_required
@permission_required("payroll_pro.change_payrollrun")
def run_finalize(request, pk):
    """
    Finalize: freeze an immutable computed snapshot on every processed
    record, denormalize totals and lock the run.
    """
    run = get_object_or_404(PayrollRun, pk=pk)
    if request.method != "POST" or run.is_locked:
        return redirect(f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}")
    total_net = 0.0
    for record in run.records.all():
        if record.pay_action != "process":
            record.computed = None
            record.gross_earnings = record.total_deductions = record.net_pay = 0
            record.save()
            continue
        revision = record.salary_revision_id or EmployeeSalary.current_for(
            record.employee_id, run.period_end
        )
        if revision is None:
            record.pay_action = "void"
            record.save()
            continue
        record.salary_revision_id = revision
        calc = services.compute_for_record(record)
        record.computed = calc
        record.gross_earnings = calc["gross_earnings"]
        record.total_deductions = calc["total_deductions"]
        record.net_pay = calc["net_pay"]
        record.save()
        total_net += calc["net_pay"]
    run.status = "completed"
    run.total_net_pay = round(total_net, 2)
    run.save()
    _stamp(run, "finalize", request.user)
    messages.success(
        request,
        _("Payroll finalized & locked. Release payslips so employees can see "
          "them under My Finance."),
    )
    return redirect(f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}")


@login_required
@permission_required("payroll_pro.change_payrollrun")
def run_reopen(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    if request.method == "POST":
        run.status = "draft"
        run.payslips_released = False
        run.save()
        messages.success(
            request, _("Run reopened for editing. Payslips are withdrawn.")
        )
    return redirect(f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}")


@login_required
@permission_required("payroll_pro.change_payrollrun")
def run_release(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    if request.method == "POST" and run.is_locked:
        run.payslips_released = True
        run.save()
        messages.success(
            request,
            _("Payslips released — employees can now see them in My Finance."),
        )
    next_url = request.POST.get("next") or (
        f"{reverse('pp-run-payroll')}?year={run.year}&month={run.month}"
    )
    return redirect(next_url)


# ===========================================================================
# 5a. PAYSLIPS (admin)
# ===========================================================================
@login_required
@permission_required("payroll_pro.view_payrollrunrecord")
def payslips_view(request):
    runs = PayrollRun.objects.filter(status="completed").order_by("year", "month")
    if not runs.exists():
        return render(request, "payroll_pro/payslips.html", {"runs": None})
    run_pk = request.GET.get("run") or runs.last().pk
    run = runs.filter(pk=run_pk).first() or runs.last()
    records = run.records.filter(pay_action="process").select_related("employee_id")
    record_pk = request.GET.get("record")
    record = records.filter(pk=record_pk).first() or records.first()
    context = {
        "runs": runs,
        "run": run,
        "records": records,
        "record": record,
        "calc": record.computed if record else None,
        "month_names": MONTH_NAMES,
    }
    return render(request, "payroll_pro/payslips.html", context)


def _record_access_allowed(request, record):
    return (
        request.user.has_perm("payroll_pro.view_payrollrunrecord")
        or record.employee_id.employee_user_id == request.user
    )


@login_required
def payslip_pdf_download(request, pk):
    """Payslip PDF, rendered from the frozen snapshot."""
    record = get_object_or_404(PayrollRunRecord, pk=pk)
    if not _record_access_allowed(request, record):
        raise Http404
    if not record.computed:
        messages.error(request, _("No computed payslip for this record."))
        return redirect("pp-payslips")
    html = render_to_string(
        "payroll_pro/payslip_pdf.html",
        _payslip_context(record),
        request=request,
    )
    try:
        import pdfkit

        pdf = pdfkit.from_string(
            html,
            False,
            options={
                "page-size": "A4",
                "encoding": "UTF-8",
                "enable-local-file-access": None,
            },
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        title = f"Payslip-{record.employee_id}-{record.run_id.month:02d}-{record.run_id.year}"
        response["Content-Disposition"] = f'attachment; filename="{title}.pdf"'
        return response
    except Exception:
        logger.exception("PDF generation failed; serving printable HTML instead")
        return HttpResponse(html)


def _payslip_context(record):
    run = record.run_id
    company = Company.objects.filter(hq=True).first() or Company.objects.first()
    return {
        "record": record,
        "run": run,
        "calc": record.computed,
        "employee": record.employee_id,
        "company": company,
        "period_label": f"{MONTH_NAMES[run.month - 1]} {run.year}",
    }


@login_required
@permission_required("payroll_pro.view_payrollrunrecord")
def payslip_send_mail(request, pk):
    """Email one payslip (PDF attached) to the employee."""
    from payroll_pro.mail import PayslipMailThread

    record = get_object_or_404(PayrollRunRecord, pk=pk)
    if not record.computed:
        messages.error(request, _("Finalize the run before emailing payslips."))
    else:
        PayslipMailThread(request, [record.pk]).start()
        messages.success(request, _("Payslip email is being sent."))
    return redirect(
        f"{reverse('pp-payslips')}?run={record.run_id_id}&record={record.pk}"
    )


@login_required
@permission_required("payroll_pro.view_payrollrunrecord")
def payslip_send_mail_all(request, run_pk):
    """Email every processed payslip of a run."""
    from payroll_pro.mail import PayslipMailThread

    run = get_object_or_404(PayrollRun, pk=run_pk)
    ids = list(
        run.records.filter(pay_action="process", computed__isnull=False).values_list(
            "pk", flat=True
        )
    )
    if not ids:
        messages.error(request, _("No computed payslips in this run."))
    else:
        PayslipMailThread(request, ids).start()
        messages.success(
            request, _("Payslip emails are being sent to %(count)d employees.")
            % {"count": len(ids)}
        )
    return redirect(f"{reverse('pp-payslips')}?run={run.pk}")


# ===========================================================================
# 5b. MY FINANCE (employee self service)
# ===========================================================================
@login_required
def my_finance_view(request):
    """
    Employee self-service. Admins with the payslip permission can inspect any
    employee via ?employee=<pk>; everyone else sees only themselves.
    """
    employee = _own_employee(request)
    requested = request.GET.get("employee")
    if requested and request.user.has_perm("payroll_pro.view_payrollrunrecord"):
        employee = Employee.objects.filter(pk=requested).first() or employee
    if employee is None:
        raise Http404

    tab = request.GET.get("tab", "summary")
    paytab = request.GET.get("paytab", "salary")
    current = EmployeeSalary.current_for(employee)
    released_records = (
        PayrollRunRecord.objects.filter(
            employee_id=employee,
            pay_action="process",
            computed__isnull=False,
            run_id__status="completed",
            run_id__payslips_released=True,
        )
        .select_related("run_id")
        .order_by("run_id__year", "run_id__month")
    )
    context = {
        "employee": employee,
        "tab": tab,
        "paytab": paytab,
        "current": current,
        "released_records": released_records,
        "latest_record": released_records.last(),
        "month_names": MONTH_NAMES,
        "can_switch": request.user.has_perm("payroll_pro.view_payrollrunrecord"),
        "employees": (
            Employee.objects.filter(is_active=True)
            if request.user.has_perm("payroll_pro.view_payrollrunrecord")
            else None
        ),
    }

    if tab == "mypay":
        if paytab == "salary":
            context["revisions"] = EmployeeSalary.objects.filter(employee_id=employee)
            if current:
                context["breakup"] = services.compute_salary(
                    current, {"present_days": 30, "lop_days": 0, "total_days": 30}
                )
        elif paytab == "payslips":
            record_pk = request.GET.get("record")
            record = (
                released_records.filter(pk=record_pk).first()
                or released_records.last()
            )
            context["record"] = record
            context["calc"] = record.computed if record else None
        elif paytab == "incometax":
            context.update(_income_tax_context(employee, current))
        elif paytab == "managetax":
            today = date.today()
            fy_start = today.year if today.month >= 4 else today.year - 1
            declaration = TaxDeclaration.objects.filter(
                employee_id=employee, financial_year_start=fy_start
            ).first()
            regime = current.effective_tax_regime() if current else None
            context.update(
                {
                    "declaration": declaration,
                    "declaration_form": TaxDeclarationForm(instance=declaration),
                    "fy_start": fy_start,
                    "regime": regime,
                    "regimes": TaxRegime.objects.all(),
                }
            )
        elif paytab == "revisions":
            context["revisions"] = EmployeeSalary.objects.filter(employee_id=employee)
        elif paytab == "reimbursement":
            claims = Reimbursement.objects.filter(
                employee_id=employee, type="reimbursement"
            )
            context["claims"] = claims
            context["claim_form"] = ReimbursementRequestForm()
            context["claim_totals"] = {
                status: claims.filter(status=status).count()
                for status in ("requested", "approved", "rejected")
            }
    return render(request, "payroll_pro/myfinance.html", context)


@login_required
def reimbursement_request(request):
    """Employee submits a reimbursement claim (stock payroll.Reimbursement,
    so approvals stay consistent with the rest of the application)."""
    if request.method != "POST":
        return redirect(f"{reverse('pp-my-finance')}?tab=mypay&paytab=reimbursement")
    employee = _own_employee(request)
    requested = request.POST.get("employee")
    if requested and request.user.has_perm("payroll_pro.view_payrollrunrecord"):
        employee = Employee.objects.filter(pk=requested).first() or employee
    form = ReimbursementRequestForm(request.POST, request.FILES)
    if form.is_valid():
        claim = form.save(commit=False)
        claim.employee_id = employee
        claim.type = "reimbursement"
        claim.status = "requested"
        _save_claim_preserving_employee(claim)
        messages.success(request, _("Reimbursement request submitted."))
    else:
        messages.error(request, form.errors.as_text())
    suffix = f"&employee={employee.pk}" if requested else ""
    return redirect(f"{reverse('pp-my-finance')}?tab=mypay&paytab=reimbursement{suffix}")


def _income_tax_context(employee, current):
    """FY income-tax computation: actual months from frozen snapshots, the
    rest projected at full attendance, remaining tax spread evenly."""
    today = date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1
    if not current:
        return {"fy_start": fy_start, "tax_cols": None}
    regime = current.effective_tax_regime()
    regime_snapshot = regime.to_snapshot() if regime else None
    declarations = services.get_declarations(employee, today)

    cols = []
    for i in range(12):
        m = ((3 + i) % 12) + 1
        y = fy_start if m >= 4 else fy_start + 1
        record = PayrollRunRecord.objects.filter(
            employee_id=employee,
            run_id__year=y,
            run_id__month=m,
            run_id__status="completed",
            computed__isnull=False,
        ).first()
        if record:
            calc = record.computed
            cols.append(
                {
                    "label": MONTH_NAMES[m - 1][:3],
                    "actual": True,
                    "gross": calc["gross_earnings"],
                    "pf": calc["employee_pf"],
                    "tds": calc["tds"],
                }
            )
        else:
            calc = services.compute_salary(
                current, {"present_days": 30, "lop_days": 0, "total_days": 30}
            )
            cols.append(
                {
                    "label": MONTH_NAMES[m - 1][:3],
                    "actual": False,
                    "gross": calc["gross_monthly"],
                    "pf": calc["employee_pf"],
                    "tds": None,
                }
            )
    annual_gross = sum(c["gross"] for c in cols)
    annual_pf = sum(c["pf"] for c in cols)
    tax_paid = sum(c["tds"] or 0 for c in cols if c["actual"])
    taxable = services.taxable_annual(
        regime_snapshot, annual_gross, annual_pf, declarations
    )
    structure = current.salary_structure_id
    total_payable = (
        services.slab_tax_annual(regime_snapshot, taxable)
        if structure.tds_enabled and regime_snapshot
        else 0.0
    )
    remaining = max(0.0, total_payable - tax_paid)
    future = [c for c in cols if not c["actual"]]
    monthly_future_tds = remaining / len(future) if future else 0.0
    for c in future:
        c["tds"] = monthly_future_tds
    return {
        "fy_start": fy_start,
        "tax_cols": cols,
        "tax_summary": {
            "taxable": taxable,
            "payable": total_payable,
            "paid": tax_paid,
            "remaining": remaining,
        },
        "regime": regime,
    }


@login_required
def tax_declaration_save(request):
    """Employee saves 80C/80D declarations for the current FY."""
    if request.method != "POST":
        return redirect(f"{reverse('pp-my-finance')}?tab=mypay&paytab=managetax")
    employee = _own_employee(request)
    requested = request.POST.get("employee")
    if requested and request.user.has_perm("payroll_pro.view_payrollrunrecord"):
        employee = Employee.objects.filter(pk=requested).first() or employee
    today = date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1
    declaration, _created = TaxDeclaration.objects.get_or_create(
        employee_id=employee, financial_year_start=fy_start
    )
    form = TaxDeclarationForm(request.POST, instance=declaration)
    if form.is_valid():
        form.save()
        messages.success(request, _("Declarations saved."))
    else:
        messages.error(request, form.errors.as_text())
    suffix = f"&employee={employee.pk}" if requested else ""
    return redirect(
        f"{reverse('pp-my-finance')}?tab=mypay&paytab=managetax{suffix}"
    )


@login_required
def tax_regime_choice_save(request):
    """
    Employee chooses their personal tax regime: recorded as a new salary
    revision row with the regime override (history preserved).
    """
    if request.method != "POST":
        return redirect(f"{reverse('pp-my-finance')}?tab=mypay&paytab=managetax")
    employee = _own_employee(request)
    requested = request.POST.get("employee")
    if requested and request.user.has_perm("payroll_pro.view_payrollrunrecord"):
        employee = Employee.objects.filter(pk=requested).first() or employee
    current = EmployeeSalary.current_for(employee)
    regime = TaxRegime.objects.filter(pk=request.POST.get("regime")).first()
    if current and regime and current.tax_regime_id != regime:
        EmployeeSalary.objects.create(
            employee_id=employee,
            salary_structure_id=current.salary_structure_id,
            tax_regime_id=regime,
            annual_ctc=current.annual_ctc,
            effective_date=date.today(),
            note=str(_("Tax regime changed to %(name)s") % {"name": regime.name}),
        )
        messages.success(request, _("Tax regime updated."))
    suffix = f"&employee={employee.pk}" if requested else ""
    return redirect(
        f"{reverse('pp-my-finance')}?tab=mypay&paytab=managetax{suffix}"
    )


# ===========================================================================
# Settings: automatic run scheduler
# ===========================================================================
@login_required
@permission_required("payroll_pro.view_payrollproautosetting")
def auto_settings_view(request):
    context = {"settings_list": PayrollProAutoSetting.objects.all()}
    return render(request, "payroll_pro/settings_auto.html", context)


@login_required
def auto_settings_form(request, pk=None):
    """Full-page create/edit form for an auto-run schedule (replaces the
    offcanvas — a real page navigation, instead of a mid-panel reload)."""
    if not request.user.has_perm("payroll_pro.change_payrollproautosetting"):
        return handle_no_permission(request)
    instance = get_object_or_404(PayrollProAutoSetting, pk=pk) if pk else None
    context = {
        "form": PayrollProAutoSettingForm(instance=instance),
        "edit_instance": instance,
    }
    return render(request, "payroll_pro/settings_auto_form.html", context)


@login_required
@permission_required("payroll_pro.change_payrollproautosetting")
def auto_settings_save(request):
    if request.method != "POST":
        return redirect("pp-auto-settings")
    pk = request.POST.get("pk")
    instance = PayrollProAutoSetting.objects.filter(pk=pk).first() if pk else None
    form = PayrollProAutoSettingForm(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, _("Auto run setting saved."))
        return redirect("pp-auto-settings")
    context = {"form": form, "edit_instance": instance}
    return render(request, "payroll_pro/settings_auto_form.html", context)


@login_required
@permission_required("payroll_pro.change_payrollproautosetting")
def auto_settings_delete(request, pk):
    setting = get_object_or_404(PayrollProAutoSetting, pk=pk)
    setting.delete()
    messages.success(request, _("Auto run setting removed."))
    return redirect("pp-auto-settings")
