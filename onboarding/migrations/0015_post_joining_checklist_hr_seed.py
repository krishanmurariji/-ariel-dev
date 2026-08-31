"""
Seeds the "Post-Joining Checklist (HR)" Task Group with the tasks from HR's
Induction & Orientation checklist document, so it's assignable from the
Post-Joining Checklist stepper step without HR having to re-type all 45
items by hand through the Task Templates UI.
"""

from django.db import migrations

TITLE = "Post-Joining Checklist (HR)"
DESCRIPTION = "HR's Induction & Orientation checklist for a new joinee's first month."

# (title, description, is_mandatory, requires_proof)
TASKS = [
    # Week 1 - General Workplace Induction
    ("Meet and greet the new joinee and provide the joining form.", "Week 1 - General Workplace Induction", True, False),
    ("Meet with the new hire to collect required documents and explain HR-related aspects.", "Week 1 - General Workplace Induction", True, False),
    ("Collect required documents (documentation).", "Week 1 - General Workplace Induction", True, False),
    ("Explain salary-related information (TDS, PF, ESI, PT, Gratuity, etc.).", "Week 1 - General Workplace Induction", True, False),
    ("Explain the duration of the probation period.", "Week 1 - General Workplace Induction", True, False),
    ("Explain leaves and attendance (work hours, working days, and shift timing).", "Week 1 - General Workplace Induction", True, False),
    ("Explain the daily work status report (format and mode).", "Week 1 - General Workplace Induction", True, False),
    ("Explain the performance assessment process.", "Week 1 - General Workplace Induction", True, False),
    ("Share the company's expectations with the new hire.", "Week 1 - General Workplace Induction", True, False),
    ("Arrange a meeting with the immediate manager to clarify job roles and responsibilities.", "Week 1 - General Workplace Induction", True, False),
    ("Arrange a meeting with an onboarding buddy to help the new hire settle in.", "Week 1 - General Workplace Induction", True, False),
    ("Introduce the new hire to the team and HR.", "Week 1 - General Workplace Induction", True, False),
    ("Show the new hire around the workplace (pantry, play area, restroom, conference/meeting rooms, etc.).", "Week 1 - General Workplace Induction", True, False),
    ("Share the company website for self-study before the presentation.", "Week 1 - General Workplace Induction", True, False),
    ("Create the new hire's file and update their details on the employee sheet.", "Week 1 - General Workplace Induction", True, False),
    ("Send an employee verification request to the previous organisation via Iservz (if experienced).", "Week 1 - General Workplace Induction. Typically handled by HR (Sonali).", True, False),
    ("Start maintaining the new hire's attendance.", "Week 1 - General Workplace Induction. Typically handled by HR (Amandeep Kaur).", True, False),
    # Day 2 - Presentation
    ("Cover Organizational Overview in the 1st presentation.", "Day 2 - Presentation", True, False),
    ("Cover Vision, Mission & Core Values in the 1st presentation.", "Day 2 - Presentation", True, False),
    ("Cover key highlights in the 1st presentation.", "Day 2 - Presentation", True, False),
    ("Cover services and technologies in the 1st presentation.", "Day 2 - Presentation", True, False),
    ("Cover organizational departments & hierarchy in the 1st presentation.", "Day 2 - Presentation", True, False),
    ("Cover HR policies (leaves & attendance, telephone usage, communication, confidentiality, and dress code) in the 1st presentation.", "Day 2 - Presentation", True, False),
    ("Cover Sexual Harassment Policy & Women Cell in the 2nd presentation.", "Day 2 - Presentation", True, False),
    ("Cover compensation, rewards, and other subsidiary benefits in the 2nd presentation.", "Day 2 - Presentation", True, False),
    ("Cover the Separation Policy in the 2nd presentation.", "Day 2 - Presentation", True, False),
    ("Cover employee benefits in the 2nd presentation.", "Day 2 - Presentation", True, False),
    ("Cover work ethics and team introduction in the 2nd presentation.", "Day 2 - Presentation", True, False),
    ("Cover recreational activities in the 2nd presentation.", "Day 2 - Presentation", True, False),
    ("Provide the joining kit (notepad, T-shirt, bottle, and pen).", "Day 2 - Presentation", True, False),
    ("Get the bond signed (freshers) or the agreement signed (experienced hires).", "Day 2 - Presentation. Bond applies to freshers only; agreement applies to experienced hires only.", True, True),
    # 2nd Week (Day 5)
    ("Collect 1st-week feedback from the immediate manager and create the official account if positive.", "2nd Week (Day 5)", True, False),
    ("Create the new hire's account on KEKA.", "2nd Week (Day 5)", True, False),
    ("Add female employees to the Women's Cell group on MS Teams and WhatsApp.", "2nd Week (Day 5). Not applicable to male employees.", False, False),
    ("Provide a KEKA demo session to the new hire.", "2nd Week (Day 5)", True, False),
    ("Enrol the new hire in the biometric system and update prior attendance on KEKA.", "2nd Week (Day 5)", True, False),
    ("Update all necessary information and documents for the new hire on KEKA.", "2nd Week (Day 5)", True, False),
    ("Create a KEKA welcome post for the new hire.", "2nd Week (Day 5)", True, False),
    ("Share the Induction & Orientation Survey Form (to be submitted within 2 working days).", "2nd Week (Day 5)", True, False),
    ("Hold an end-of-week meeting to address the new hire's concerns or queries.", "2nd Week (Day 5)", True, False),
    ("Ask the new hire to review the HR policy and other documents on KEKA (overtime policy, self-assessment, etc.).", "2nd Week (Day 5)", True, False),
    ("Obtain the new hire's signature on the confidentiality acknowledgement (salary, promotions, HR discussions, etc.).", "2nd Week (Day 5)", True, True),
    # End of the Month (Last week follow-up)
    ("Email the reporting manager requesting a performance assessment (to be submitted within 2-3 working days).", "End of the Month - Last Week Follow-up", True, False),
    ("Schedule a meeting with the reporting manager and new hire to share the assessment results.", "End of the Month - Last Week Follow-up. A positive assessment leads to a confirmation letter; otherwise, decide whether to extend the probation period.", True, False),
    ("Obtain signatures on the HR Policy acknowledgement and Performance Review acknowledgement.", "End of the Month - Last Week Follow-up", True, True),
]


def seed_forward(apps, schema_editor):
    TaskTemplate = apps.get_model("onboarding", "TaskTemplate")
    TaskTemplateTask = apps.get_model("onboarding", "TaskTemplateTask")

    template, _created = TaskTemplate.objects.get_or_create(
        title=TITLE, defaults={"description": DESCRIPTION}
    )
    for sequence, (title, description, is_mandatory, requires_proof) in enumerate(
        TASKS, start=1
    ):
        TaskTemplateTask.objects.get_or_create(
            template_id=template,
            title=title,
            defaults={
                "description": description,
                "is_mandatory": is_mandatory,
                "requires_proof": requires_proof,
                "sequence": sequence,
            },
        )


def seed_backward(apps, schema_editor):
    TaskTemplate = apps.get_model("onboarding", "TaskTemplate")
    TaskTemplate.objects.filter(title=TITLE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0014_alter_candidatechecklisttask_phase"),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_backward),
    ]
