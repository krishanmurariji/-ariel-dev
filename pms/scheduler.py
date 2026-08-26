from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dateutil.relativedelta import relativedelta

from notifications.signals import notify


def cyclic_feedback_creation():
    from pms.models import Feedback

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

            feedback.cyclic_feedback = False
            feedback.save()

    return


def probation_feedback_creation():
    """
    Walks active ProbationFeedbackCycle rows and generates the next month's
    Feedback once it's due - buddy is added as a reviewer for month 1 only,
    every month gets the employee's *current* reporting manager. A cycle
    started on a candidate before they're converted to an employee (or
    before they have a reporting manager set) is simply retried the next
    day, rather than erroring or giving up.
    """
    from employee.models import Employee
    from onboarding.stepper import _candidate_own_employee_id
    from pms.models import Feedback, ProbationFeedbackCycle

    today = date.today()
    cycles = ProbationFeedbackCycle.objects.filter(is_active=True).select_related(
        "candidate_id", "buddy_id", "question_template_id"
    )
    for cycle in cycles:
        due_date = cycle.start_date + relativedelta(months=cycle.months_completed + 1)
        if today < due_date or cycle.months_completed >= cycle.total_months:
            continue

        employee_id = _candidate_own_employee_id(cycle.candidate_id)
        if not employee_id:
            continue  # not converted to an employee yet - try again next day

        employee = (
            Employee.objects.filter(pk=employee_id)
            .select_related("employee_work_info")
            .first()
        )
        work_info = getattr(employee, "employee_work_info", None) if employee else None
        reporting_manager = (
            getattr(work_info, "reporting_manager_id", None) if work_info else None
        )
        if not reporting_manager:
            continue  # no reporting manager set yet - try again next day

        month_number = cycle.months_completed + 1
        feedback = Feedback(
            review_cycle=f"Probation Review - Month {month_number}",
            employee_id=employee,
            manager_id=reporting_manager,
            question_template_id=cycle.question_template_id,
            start_date=today,
            end_date=today + relativedelta(days=7),
        )
        feedback.save()

        if month_number == 1 and cycle.buddy_id:
            feedback.others_id.add(cycle.buddy_id)

        _notify_probation_feedback(feedback)

        cycle.months_completed = month_number
        if cycle.months_completed >= cycle.total_months:
            cycle.is_active = False
        cycle.save()


def _notify_probation_feedback(feedback):
    """
    Mirrors pms/views.py's send_feedback_notifications, minus the request
    object (none available in a background job) - the Feedback instance
    itself is used as the notification sender, matching the no-request
    pattern already used above for the recruitment closing-reminder job.
    """
    from django.urls import reverse

    if feedback.employee_id.employee_user_id:
        notify.send(
            feedback,
            recipient=feedback.employee_id.employee_user_id,
            verb="You have received feedback!",
            verb_ar="لقد تلقيت ملاحظات!",
            verb_de="Sie haben Feedback erhalten!",
            verb_es="¡Has recibido retroalimentación!",
            verb_fr="Vous avez reçu des commentaires !",
            redirect=reverse("feedback-detailed-view", kwargs={"id": feedback.id}),
            icon="chatbox-ellipses",
        )

    reviewers = [feedback.manager_id] + list(feedback.others_id.all())
    recipients = [r.employee_user_id for r in reviewers if r and r.employee_user_id]
    if recipients:
        notify.send(
            feedback,
            recipient=recipients,
            verb="You have been requested to provide feedback!",
            verb_ar="لقد طُلب منك تقديم ملاحظات!",
            verb_de="Sie wurden gebeten, Feedback zu geben!",
            verb_es="Se le ha solicitado que proporcione comentarios.",
            verb_fr="Il vous a été demandé de fournir des commentaires.",
            redirect=reverse("feedback-detailed-view", kwargs={"id": feedback.id}),
            icon="chatbox-ellipses",
        )


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
