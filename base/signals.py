from datetime import datetime

from django.apps import apps
from django.db.models import Max, Q
from django.db.models.signals import m2m_changed, post_delete, post_migrate, post_save
from django.dispatch import receiver

from base.models import Announcement, PenaltyAccounts
from horilla.methods import get_horilla_model_class


@receiver(post_save, sender=PenaltyAccounts)
def create_deduction_cutleave_from_penalty(sender, instance, created, **kwargs):
    """
    This is post save method, used to create deduction and cut available leave days
    """
    # only work when creating
    if created:
        penalty_amount = instance.penalty_amount
        if apps.is_installed("payroll") and penalty_amount:
            Deduction = get_horilla_model_class(app_label="payroll", model="deduction")
            penalty = Deduction()
            if instance.late_early_id:
                penalty.title = f"{instance.late_early_id.get_type_display()} penalty"
                penalty.one_time_date = (
                    instance.late_early_id.attendance_id.attendance_date
                )
            elif instance.leave_request_id:
                penalty.title = f"Leave penalty {instance.leave_request_id.end_date}"
                penalty.one_time_date = instance.leave_request_id.end_date
            else:
                penalty.title = f"Penalty on {datetime.today()}"
                penalty.one_time_date = datetime.today()
            penalty.include_active_employees = False
            penalty.is_fixed = True
            penalty.amount = instance.penalty_amount
            penalty.only_show_under_employee = True
            penalty.save()
            penalty.include_active_employees = False
            penalty.specific_employees.add(instance.employee_id)
            penalty.save()

        if (
            apps.is_installed("leave")
            and instance.leave_type_id
            and instance.minus_leaves
        ):
            available = instance.employee_id.available_leave.filter(
                leave_type_id=instance.leave_type_id
            ).first()
            unit = round(instance.minus_leaves * 2) / 2
            if not instance.deduct_from_carry_forward:
                available.available_days = max(0, (available.available_days - unit))
            else:
                available.carryforward_days = max(
                    0, (available.carryforward_days - unit)
                )

            available.save()


@receiver(post_delete, sender=PenaltyAccounts)
def delete_deduction_cutleave_from_penalty(sender, instance, **kwargs):
    """
    This is a post delete method, used to delete the deduction and update available leave days.
    """
    # Check if the deduction model is installed
    if apps.is_installed("payroll"):
        Deduction = get_horilla_model_class(app_label="payroll", model="deduction")

        if instance.late_early_id:
            title = f"{instance.late_early_id.get_type_display()} penalty"
        elif instance.leave_request_id:
            title = f"Leave penalty {instance.leave_request_id.end_date}"
        else:
            title = f"Penalty on {datetime.today()}"

        # Attempt to retrieve the deduction specifically associated with the penalty account
        deductions = Deduction.objects.filter(
            specific_employees=instance.employee_id,
            amount=instance.penalty_amount,
            title=title,
        )

        # If you have a date or other unique field, add it to the filter
        if instance.late_early_id:
            deductions = deductions.filter(
                one_time_date=instance.late_early_id.attendance_id.attendance_date
            )
        elif instance.leave_request_id:
            deductions = deductions.filter(
                one_time_date=instance.leave_request_id.end_date
            )
        else:
            deductions = deductions.filter(one_time_date=datetime.today())

        for deduction in deductions:
            deduction.delete()

    if apps.is_installed("leave") and instance.leave_type_id and instance.minus_leaves:
        available = instance.employee_id.available_leave.filter(
            leave_type_id=instance.leave_type_id
        ).first()
        if available:
            unit = round(instance.minus_leaves * 2) / 2
            if not instance.deduct_from_carry_forward:
                available.available_days += unit  # Restore the deducted days
            else:
                available.carryforward_days += (
                    unit  # Restore the deducted carryforward days
                )

            available.save()


# @receiver(post_migrate)
def clean_work_records(sender, **kwargs):
    if sender.label not in ["attendance"]:
        return
    from attendance.models import WorkRecords

    latest_records = (
        WorkRecords.objects.exclude(work_record_type="DFT")
        .values("employee_id", "date")
        .annotate(latest_id=Max("id"))
    )

    # Delete all but the latest WorkRecord
    deleted_count = 0
    for record in latest_records:
        deleted_count += (
            WorkRecords.objects.filter(
                employee_id=record["employee_id"], date=record["date"]
            )
            .exclude(id=record["latest_id"])
            .delete()[0]
        )


@receiver(post_migrate)
def create_shift_days(sender, **kwargs):
    from base.models import EmployeeShiftDay

    if not EmployeeShiftDay.objects.exists():
        days = [
            ("monday", "Monday"),
            ("tuesday", "Tuesday"),
            ("wednesday", "Wednesday"),
            ("thursday", "Thursday"),
            ("friday", "Friday"),
            ("saturday", "Saturday"),
            ("sunday", "Sunday"),
        ]

        EmployeeShiftDay.objects.bulk_create(
            [EmployeeShiftDay(day=day[0]) for day in days]
        )


@receiver(m2m_changed, sender=Announcement.employees.through)
def filtered_employees(sender, instance, action, **kwargs):
    """
    filtered employees
    """
    if action not in ["post_add", "post_remove", "post_clear"]:
        return  # Only run after M2M changes
    employee_ids = list(instance.employees.values_list("id", flat=True))
    department_ids = list(instance.department.values_list("id", flat=True))
    job_position_ids = list(instance.job_position.values_list("id", flat=True))

    employees = instance.model_employee.objects.filter(
        Q(id__in=employee_ids)
        | Q(employee_work_info__department_id__in=department_ids)
        | Q(employee_work_info__job_position_id__in=job_position_ids)
    )

    instance.filtered_employees.set(employees)



# Login brute-force protection (5+ failed attempts locking out a
# username+IP pair) is now handled by django-axes - see AUTHENTICATION_
# BACKENDS, MIDDLEWARE, and the AXES_* settings in horilla/settings/
# base.py. It used to live here as a hand-rolled Fail2BanMiddleware/
# log_login_failed pair, but that was effectively dead code: it only ran
# if a "security.log" file happened to exist (gated on a LOGGING config
# that was never set up anywhere in this project), and even enabled, it
# banned by session key rather than username or IP, so simply dropping
# the session cookie reset the attempt counter.
