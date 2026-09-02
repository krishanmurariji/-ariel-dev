"""
Employee Checklist tasks were being saved with a blank assignee_id
(onboarding/stepper.py's _save_checklist_tasks, before it was changed to
resolve the candidate's own Employee record) - this backfills existing
rows so "My Tasks" shows a real assignee for them instead of blank,
matching the same converted_employee_id-then-email-match lookup used
elsewhere for a candidate's own Employee record.
"""

from django.db import migrations


def backfill_assignee(apps, schema_editor):
    CandidateChecklistTask = apps.get_model("onboarding", "CandidateChecklistTask")
    Employee = apps.get_model("employee", "Employee")

    tasks = CandidateChecklistTask.objects.filter(
        phase="employee_checklist", assignee_id__isnull=True
    ).select_related("candidate_id")

    employee_by_email = {}
    for task in tasks:
        candidate = task.candidate_id
        employee = None
        if candidate.converted_employee_id_id:
            employee = Employee.objects.filter(
                pk=candidate.converted_employee_id_id
            ).first()
        if not employee:
            if candidate.email not in employee_by_email:
                employee_by_email[candidate.email] = Employee.objects.filter(
                    email=candidate.email
                ).first()
            employee = employee_by_email[candidate.email]
        if employee:
            task.assignee_id = employee
            task.save(update_fields=["assignee_id"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0018_swap_furthest_step_key_for_reorder"),
    ]

    operations = [
        migrations.RunPython(backfill_assignee, noop),
    ]
