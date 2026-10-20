from django.db import migrations

RESIGNATION_REASONS = [
    "Explore Other Careers",
    "Other",
    "Personal Reasons",
    "Relocating",
    "Resignation",
    "Retirement",
]
TERMINATION_REASONS = [
    "Performance",
    "Misconduct",
    "Absconding",
    "Redundancy",
    "End of Contract",
    "Other",
]


def forwards(apps, schema_editor):
    ExitReasonOption = apps.get_model("offboarding", "ExitReasonOption")
    OffboardingEmployee = apps.get_model("offboarding", "OffboardingEmployee")

    # Starter list of reasons (global), only when nobody has set any up yet.
    if not ExitReasonOption.objects.exists():
        for exit_type, titles in (
            ("resignation", RESIGNATION_REASONS),
            ("termination", TERMINATION_REASONS),
        ):
            for title in titles:
                ExitReasonOption.objects.create(title=title, exit_type=exit_type)

    # People already in the old kanban pipeline: Archived stage -> Exited,
    # every other stage -> Exit in progress.
    for case in OffboardingEmployee.objects.select_related("stage_id").all():
        archived = bool(case.stage_id_id and case.stage_id.type == "archived")
        case.status = "exited" if archived else "in_progress"
        case.last_working_day = case.notice_period_ends
        case.notice_date = case.notice_period_starts
        if archived:
            case.exited_on = case.notice_period_ends
        case.save(
            update_fields=["status", "last_working_day", "notice_date", "exited_on"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("offboarding", "0006_exit_process_foundation"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
