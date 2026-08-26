"""
Seeds a global "Probation Review" QuestionTemplate (company_id left empty,
which QuestionTemplate treats as "all companies") with a handful of starter
questions, so the Probation Feedback Cycle feature always has a template to
use out of the box. Editable/extendable afterward through the normal
Question Template UI like any other template.
"""

from django.db import migrations

TEMPLATE_TITLE = "Probation Review"
QUESTIONS = [
    ("How well has the employee adapted to the team and role so far?", "1"),  # Text
    ("Rate the employee's overall performance this period.", "2"),  # Rating
    ("Has the employee met the expectations set for this stage?", "3"),  # Boolean
    ("Rate the employee's communication and collaboration.", "2"),  # Rating
    ("Any additional feedback or concerns to flag?", "1"),  # Text
]


def seed(apps, schema_editor):
    QuestionTemplate = apps.get_model("pms", "QuestionTemplate")
    Question = apps.get_model("pms", "Question")

    template, _created = QuestionTemplate.objects.get_or_create(
        question_template=TEMPLATE_TITLE
    )
    for text, qtype in QUESTIONS:
        Question.objects.get_or_create(
            template_id=template, question=text, defaults={"question_type": qtype}
        )


def unseed(apps, schema_editor):
    apps.get_model("pms", "QuestionTemplate").objects.filter(
        question_template=TEMPLATE_TITLE
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pms", "0005_probationfeedbackcycle"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
