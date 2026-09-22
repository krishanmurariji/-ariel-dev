from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dateutil.relativedelta import relativedelta


def cyclic_feedback_creation():
    from pms.models import Feedback
    from pms.views import send_feedback_notifications

    feedbacks = Feedback.objects.filter(cyclic_next_start_date=datetime.today().date())
    for feedback in feedbacks:
        if feedback.cyclic_feedback:
            feedback_obj = Feedback()
            for field in feedback._meta.fields:
                if field.name not in [
                    "id",
                    "cyclic_next_start_date",
                    "cyclic_next_end_date",
                ]:
                    setattr(feedback_obj, field.name, getattr(feedback, field.name))
            title = (
                f"{feedback_obj.review_cycle.split('- cyclic')[0]} - cyclic {feedback_obj.start_date}"
                if "- cyclic" in feedback_obj.review_cycle
                else f"{feedback_obj.review_cycle} - cyclic {feedback_obj.start_date}"
            )
            feedback_obj.review_cycle = title
            feedback_obj.status = "Not Started"
            feedback_obj.start_date = feedback.cyclic_next_start_date
            feedback_obj.end_date = feedback.cyclic_next_end_date
            feedback_obj.save()
            # M2M fields (subordinate/colleague/others) aren't copied by
            # the field-by-field loop above - request_employees() and the
            # notification/email below would otherwise silently miss them
            # for every auto-recreated cycle after the first.
            feedback_obj.subordinate_id.set(feedback.subordinate_id.all())
            feedback_obj.colleague_id.set(feedback.colleague_id.all())
            feedback_obj.others_id.set(feedback.others_id.all())

            send_feedback_notifications(None, feedback_obj)

            feedback.cyclic_feedback = False
            feedback.save()

    return


def probation_feedback_creation():
    """
    Walks active ProbationFeedbackCycle rows and generates the next month's
    Feedback once it's due - the reporting manager, buddy, and colleague
    are whoever was chosen at setup time (ProbationFeedbackCycle), not
    re-resolved from the employee's current work info; buddy and colleague
    are added as reviewers for month 1 only, the reporting manager every
    month. A cycle started on a candidate before they're converted to an
    employee is simply retried the next day, rather than erroring or
    giving up.
    """
    from employee.models import Employee
    from onboarding.stepper import _candidate_own_employee_id
    from pms.models import Feedback, ProbationFeedbackCycle

    today = date.today()
    cycles = ProbationFeedbackCycle.objects.filter(is_active=True).select_related(
        "candidate_id",
        "reporting_manager_id",
        "buddy_id",
        "colleague_id",
        "question_template_id",
        "employee_question_template_id",
    )
    for cycle in cycles:
        due_date = cycle.start_date + relativedelta(months=cycle.months_completed + 1)
        if today < due_date or cycle.months_completed >= cycle.total_months:
            continue

        employee_id = _candidate_own_employee_id(cycle.candidate_id)
        if not employee_id:
            continue  # not converted to an employee yet - try again next day

        employee = Employee.objects.filter(pk=employee_id).first()
        if not cycle.reporting_manager_id:
            continue  # no reporting manager captured - try again next day

        month_number = cycle.months_completed + 1
        feedback = Feedback(
            review_cycle=f"Probation Review - Month {month_number}",
            employee_id=employee,
            manager_id=cycle.reporting_manager_id,
            question_template_id=cycle.question_template_id,
            employee_question_template_id=cycle.employee_question_template_id,
            start_date=today,
            end_date=today + relativedelta(days=7),
        )
        feedback.save()

        if month_number == 1:
            if cycle.buddy_id:
                feedback.others_id.add(cycle.buddy_id)
            if cycle.colleague_id:
                feedback.colleague_id.add(cycle.colleague_id)

        from pms.views import send_feedback_notifications

        send_feedback_notifications(None, feedback)

        cycle.months_completed = month_number
        if cycle.months_completed >= cycle.total_months:
            cycle.is_active = False
        cycle.save()


scheduler = BackgroundScheduler()
cron_trigger = CronTrigger(hour=8)
grace_time_seconds = int(timedelta(days=1).total_seconds())
scheduler.add_job(
    cyclic_feedback_creation, cron_trigger, misfire_grace_time=grace_time_seconds
)
scheduler.add_job(
    probation_feedback_creation, cron_trigger, misfire_grace_time=grace_time_seconds
)

scheduler.start()
