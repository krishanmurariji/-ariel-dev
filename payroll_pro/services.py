"""
payroll_pro/services.py

Payroll Pro calculation engine.

Pure functions that take a salary revision (employee + structure + CTC +
regime) and the month's attendance figures and produce a fully JSON-safe
computation dict. The same dict is used for live previews in the Run Payroll
wizard and is frozen onto ``PayrollRunRecord.computed`` at finalize time so
that payslip history survives any later structure / regime edits.

Attendance and LOP are sourced from the existing attendance and leave tables
through the same helpers the stock payroll module uses
(``base.methods.get_working_days`` and ``payroll.methods.methods.get_leaves``).
"""

import calendar
import logging
from datetime import date

from django.apps import apps

from base.methods import get_working_days
from payroll_pro.models import EmployeeSalary, TaxDeclaration

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Attendance / LOP from existing tables
# ---------------------------------------------------------------------------
def month_period(year: int, month: int):
    """(start_date, end_date) of the calendar month."""
    return (
        date(year, month, 1),
        date(year, month, calendar.monthrange(year, month)[1]),
    )


def get_month_working_days(year: int, month: int, employee=None) -> float:
    """
    Actual working days of the month from the company calendar (calendar days
    minus holidays / company leaves), via ``base.methods.get_working_days``.
    """
    start_date, end_date = month_period(year, month)
    try:
        data = get_working_days(start_date, end_date, employee)
        return float(data.get("total_working_days") or 0)
    except Exception:
        logger.exception("Working days lookup failed; falling back to calendar days")
        return float((end_date - start_date).days + 1)


def get_attendance_and_lop(employee, year: int, month: int) -> dict:
    """
    Attendance summary for the employee for the month, from the existing
    attendance and leave tables:

    - ``total_days``: working days in the month (holiday calendar aware)
    - ``lop_days``: unpaid (loss-of-pay) leave days from approved leave
      requests, exactly as the stock payroll module counts them
    - ``present_days``: total_days − lop_days
    """
    start_date, end_date = month_period(year, month)
    total_days = get_month_working_days(year, month, employee)
    lop_days = 0.0
    if apps.is_installed("leave"):
        try:
            from payroll.methods.methods import get_leaves

            leave_data = get_leaves(employee, start_date, end_date)
            lop_days = float(leave_data.get("unpaid_leaves") or 0)
            # Partial-pay (custom percentage) leaves count their unpaid share.
            for _leave_date, pct in leave_data.get("custom_leave_dates", []):
                lop_days += max(0.0, 1.0 - (float(pct or 0) / 100.0))
        except Exception:
            logger.exception("Leave lookup failed for %s", employee)
    lop_days = min(lop_days, total_days)
    return {
        "total_days": total_days,
        "lop_days": lop_days,
        "present_days": max(0.0, total_days - lop_days),
    }


# ---------------------------------------------------------------------------
# Gross derivation
# ---------------------------------------------------------------------------
def safe_eval_gross_formula(expression, monthly_ctc):
    """
    Evaluate a restricted arithmetic formula with the single variable CTC.
    Returns None when the expression is missing or invalid.
    """
    if not expression or not isinstance(expression, str):
        return None
    import re

    if not re.fullmatch(r"[0-9+\-*/().\sCTCctc]+", expression):
        return None
    python_expr = re.sub("ctc", "(__ctc)", expression, flags=re.IGNORECASE)
    if re.search(r"[a-zA-Z]", python_expr.replace("__ctc", "")):
        return None
    try:
        value = eval(  # noqa: S307 - input restricted to arithmetic + CTC above
            python_expr, {"__builtins__": {}}, {"__ctc": float(monthly_ctc)}
        )
        return float(value) if isinstance(value, (int, float)) else None
    except Exception:
        return None


def _basic_pct_of(structure_snapshot: dict) -> float:
    for comp in structure_snapshot.get("components", []):
        if comp.get("is_basic") and comp.get("component_type") == "pct_gross":
            return float(comp.get("value") or 0)
    return 40.0


def derive_gross(structure_snapshot: dict, monthly_ctc: float) -> float:
    """Monthly gross from monthly CTC per the structure's gross rule."""
    mode = structure_snapshot.get("gross_mode") or "auto"
    if mode == "pct":
        return max(0.0, monthly_ctc * float(structure_snapshot.get("gross_pct") or 100) / 100)
    if mode == "formula":
        value = safe_eval_gross_formula(
            structure_snapshot.get("gross_formula"), monthly_ctc
        )
        if value is not None:
            return max(0.0, value)
    pf = structure_snapshot.get("pf", {})
    include_pf = structure_snapshot.get("include_employer_pf_in_ctc", True)
    denominator = 1.0
    if include_pf and pf.get("enabled"):
        denominator += (float(pf.get("employer_pct") or 0) / 100.0) * (
            _basic_pct_of(structure_snapshot) / 100.0
        )
    return monthly_ctc / denominator if denominator else monthly_ctc


def compute_components(structure_snapshot: dict, gross: float):
    """
    Full-month value for every component, in order:
    % of gross first (fixes Basic), then % of basic and flats, then the
    balancing component absorbs the remainder.

    A structure can have zero earning components (e.g. a consultant
    structure that only needs PT/TDS, with no Basic/HRA breakup) — in that
    case the full derived gross is paid out as a single synthetic line
    rather than silently disappearing (nothing would otherwise sum to it).
    """
    if not structure_snapshot.get("components"):
        return (
            [
                {
                    "name": "Gross Pay",
                    "component_type": "balance",
                    "value": 0.0,
                    "is_basic": False,
                    "full": gross,
                }
            ],
            0.0,
        )
    items = [
        {
            "name": comp["name"],
            "component_type": comp["component_type"],
            "value": float(comp.get("value") or 0),
            "is_basic": bool(comp.get("is_basic")),
            "full": 0.0,
        }
        for comp in structure_snapshot.get("components", [])
    ]
    basic = 0.0
    for item in items:
        if item["component_type"] == "pct_gross":
            item["full"] = gross * item["value"] / 100.0
            if item["is_basic"]:
                basic = item["full"]
    for item in items:
        if item["component_type"] == "pct_basic":
            item["full"] = basic * item["value"] / 100.0
        elif item["component_type"] == "flat":
            item["full"] = item["value"]
    fixed_total = sum(
        item["full"] for item in items if item["component_type"] != "balance"
    )
    for item in items:
        if item["component_type"] == "balance":
            item["full"] = max(0.0, gross - fixed_total)
    return items, basic


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------
def slab_tax_annual(regime_snapshot: dict, taxable: float) -> float:
    """Annual slab tax with rebate limit and cess, from the regime's slabs."""
    if not regime_snapshot:
        return 0.0
    tax = 0.0
    for slab in regime_snapshot.get("slabs", []):
        lower = float(slab.get("min_amount") or 0)
        upper = slab.get("max_amount")
        upper = float(upper) if upper is not None else None
        rate = float(slab.get("rate_pct") or 0)
        if taxable > lower:
            top = min(taxable, upper) if upper is not None else taxable
            tax += (top - lower) * rate / 100.0
    rebate_limit = float(regime_snapshot.get("rebate_limit") or 0)
    if rebate_limit and taxable <= rebate_limit:
        tax = 0.0
    cess_pct = float(regime_snapshot.get("cess_pct") or 0)
    return tax * (1 + cess_pct / 100.0)


def taxable_annual(
    regime_snapshot: dict,
    annual_gross: float,
    annual_employee_pf: float,
    declarations: dict,
) -> float:
    """
    Annual taxable income per the regime's deduction rules.

    Section 80C/80D-style investment declarations are not applied here —
    that feature (with its statutory caps) has been removed pending a
    rebuild; ``annual_employee_pf``/``declarations`` are accepted for
    signature compatibility with callers but currently unused.
    """
    if not regime_snapshot:
        return max(0.0, annual_gross)
    taxable = annual_gross - float(regime_snapshot.get("standard_deduction") or 0)
    return max(0.0, taxable)


def get_declarations(employee, on_date=None) -> dict:
    """Declared 80C/80D amounts for the financial year of ``on_date``."""
    on_date = on_date or date.today()
    fy_start = on_date.year if on_date.month >= 4 else on_date.year - 1
    declaration = TaxDeclaration.objects.filter(
        employee_id=employee, financial_year_start=fy_start
    ).first()
    if not declaration:
        return {"section_80c": 0.0, "section_80d": 0.0, "proof_submitted": False}
    return {
        "section_80c": declaration.section_80c,
        "section_80d": declaration.section_80d,
        "proof_submitted": declaration.proof_submitted,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def compute_salary(revision: EmployeeSalary, options: dict) -> dict:
    """
    Compute one employee-month. ``options`` keys (all optional):
    present_days, lop_days, total_days, calendar_days, bonus, overtime,
    reimbursement, adhoc_pay, adhoc_deduction, override_pt, override_tds,
    on_date.

    Returns a JSON-safe dict that fully describes the calculation, including
    frozen copies of the structure and regime used.
    """
    opts = {
        "present_days": 0.0,
        "lop_days": 0.0,
        "total_days": 30.0,
        "calendar_days": None,
        "bonus": 0.0,
        "overtime": 0.0,
        "reimbursement": 0.0,
        "adhoc_pay": 0.0,
        "adhoc_deduction": 0.0,
        "override_pt": None,
        "override_tds": None,
        "on_date": None,
    }
    opts.update({k: v for k, v in (options or {}).items() if v is not None or k in ("override_pt", "override_tds")})

    structure = revision.salary_structure_id
    structure_snapshot = structure.to_snapshot()
    regime = revision.effective_tax_regime()
    regime_snapshot = regime.to_snapshot() if regime else None
    if revision.tax_regime_id:
        # keep the employee-level override in the frozen copy
        structure_snapshot["tax_regime"] = regime_snapshot

    monthly_ctc = revision.monthly_ctc
    gross_monthly = derive_gross(structure_snapshot, monthly_ctc)
    components, full_basic = compute_components(structure_snapshot, gross_monthly)

    total_days = float(opts["total_days"] or 0)
    lop_days = float(opts["lop_days"] or 0)
    payable_days = max(0.0, total_days - lop_days)
    factor = (payable_days / total_days) if total_days > 0 else 0.0

    # LOP transparency figures (Calendar Days / Per-Day Wage / LOP Ratio).
    # Algebraically identical to the `factor`-based proration above — these
    # are display-only breakdown figures, never re-applied to net_pay.
    calendar_days = float(opts.get("calendar_days") or total_days or 30)
    per_day_wage = (gross_monthly / calendar_days) if calendar_days else 0.0
    lop_ratio = (calendar_days / total_days) if total_days > 0 else 0.0

    for comp in components:
        comp["prorated"] = comp["full"] * factor
    prorated_gross = sum(comp["prorated"] for comp in components)
    prorated_basic = next(
        (comp["prorated"] for comp in components if comp["is_basic"]), 0.0
    )
    lop_deduction_amount = round(gross_monthly - prorated_gross, 2)

    bonus = float(opts["bonus"] or 0)
    overtime = float(opts["overtime"] or 0)
    reimbursement = float(opts["reimbursement"] or 0)
    adhoc_pay = float(opts["adhoc_pay"] or 0)
    additions = bonus + overtime + reimbursement + adhoc_pay
    gross_earnings = prorated_gross + additions

    # --- statutory deductions ---
    pf_conf = structure_snapshot.get("pf", {})
    if pf_conf.get("enabled"):
        pf_base = prorated_basic
        if pf_conf.get("ceiling_enabled"):
            pf_base = min(pf_base, float(pf_conf.get("wage_ceiling") or 0))
        employee_pf = pf_base * float(pf_conf.get("employee_pct") or 0) / 100.0
        employer_pf = pf_base * float(pf_conf.get("employer_pct") or 0) / 100.0
        annual_pf_base = full_basic
        if pf_conf.get("ceiling_enabled"):
            annual_pf_base = min(annual_pf_base, float(pf_conf.get("wage_ceiling") or 0))
        annual_employee_pf = (
            annual_pf_base * float(pf_conf.get("employee_pct") or 0) / 100.0 * 12
        )
    else:
        employee_pf = employer_pf = annual_employee_pf = 0.0

    gratuity_conf = structure_snapshot.get("gratuity", {})
    if gratuity_conf.get("enabled"):
        employer_gratuity = (
            prorated_basic * float(gratuity_conf.get("pct") or 0) / 100.0
        )
    else:
        employer_gratuity = 0.0

    pt_conf = structure_snapshot.get("pt", {})
    if opts["override_pt"] is not None:
        pt_amount = float(opts["override_pt"])
        pt_overridden = True
    else:
        pt_amount = (
            float(pt_conf.get("amount") or 0)
            if pt_conf.get("enabled")
            and prorated_gross > float(pt_conf.get("threshold") or 0)
            else 0.0
        )
        pt_overridden = False

    declarations = get_declarations(
        revision.employee_id, opts.get("on_date") or revision.effective_date
    )
    annual_gross = gross_monthly * 12
    if opts["override_tds"] is not None:
        tds_amount = float(opts["override_tds"])
        tds_overridden = True
    elif structure_snapshot.get("tds_enabled") and regime_snapshot:
        taxable = taxable_annual(
            regime_snapshot, annual_gross, annual_employee_pf, declarations
        )
        tds_amount = slab_tax_annual(regime_snapshot, taxable) / 12.0
        tds_overridden = False
    else:
        tds_amount = 0.0
        tds_overridden = False

    adhoc_deduction = float(opts["adhoc_deduction"] or 0)
    total_deductions = employee_pf + pt_amount + tds_amount + adhoc_deduction
    net_pay = gross_earnings - total_deductions
    total_employer_cost = gross_earnings + employer_pf + employer_gratuity

    return {
        "annual_ctc": revision.annual_ctc,
        "monthly_ctc": round(monthly_ctc, 2),
        "gross_monthly": round(gross_monthly, 2),
        "structure": structure_snapshot,
        "regime_name": regime_snapshot["name"] if regime_snapshot else None,
        "declarations": declarations,
        "total_days": total_days,
        "calendar_days": calendar_days,
        "present_days": float(opts["present_days"] or 0),
        "lop_days": lop_days,
        "payable_days": payable_days,
        "factor": round(factor, 6),
        "per_day_wage": round(per_day_wage, 2),
        "lop_ratio": round(lop_ratio, 4),
        "lop_deduction_amount": lop_deduction_amount,
        "components": [
            {
                "name": comp["name"],
                "component_type": comp["component_type"],
                "value": comp["value"],
                "is_basic": comp["is_basic"],
                "full": round(comp["full"], 2),
                "prorated": round(comp["prorated"], 2),
            }
            for comp in components
        ],
        "prorated_gross": round(prorated_gross, 2),
        "prorated_basic": round(prorated_basic, 2),
        "bonus": round(bonus, 2),
        "overtime": round(overtime, 2),
        "reimbursement": round(reimbursement, 2),
        "adhoc_pay": round(adhoc_pay, 2),
        "additions": round(additions, 2),
        "gross_earnings": round(gross_earnings, 2),
        "employee_pf": round(employee_pf, 2),
        "employer_pf": round(employer_pf, 2),
        "employer_gratuity": round(employer_gratuity, 2),
        "pt": round(pt_amount, 2),
        "tds": round(tds_amount, 2),
        "pt_overridden": pt_overridden,
        "tds_overridden": tds_overridden,
        "adhoc_deduction": round(adhoc_deduction, 2),
        "total_deductions": round(total_deductions, 2),
        "net_pay": round(net_pay, 2),
        "total_employer_cost": round(total_employer_cost, 2),
    }


def compute_for_record(record, on_date=None) -> dict:
    """
    Compute (or return the frozen copy of) a payroll run record.
    Locked runs always serve the frozen snapshot.
    """
    if record.run_id.is_locked and record.computed:
        return record.computed
    revision = record.salary_revision_id or EmployeeSalary.current_for(
        record.employee_id, on_date or record.run_id.period_end
    )
    if revision is None:
        return None
    return compute_salary(
        revision,
        {
            "present_days": record.present_days,
            "lop_days": record.lop_days,
            "total_days": record.run_id.total_days,
            "calendar_days": calendar.monthrange(
                record.run_id.year, record.run_id.month
            )[1],
            "bonus": record.bonus,
            "overtime": record.overtime,
            "reimbursement": record.reimbursement,
            "adhoc_pay": record.adhoc_pay,
            "adhoc_deduction": record.adhoc_deduction,
            "override_pt": record.override_pt,
            "override_tds": record.override_tds,
            "on_date": record.run_id.period_end,
        },
    )
