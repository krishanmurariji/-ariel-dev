"""
Renames the literal "Recruitment Team" mail-footer signature to "Talent
Acquisition Team" wherever it's baked directly into a stored
HorillaMailTemplate body (older templates, or ones an admin hand-edited,
predate the shared letterhead wrapper's own team_label - see
recruitment/views/views.py and recruitment/scheduler.py for the
matching code-level rename of team_label="Recruitment Team").

Case-sensitive on purpose: some bodies also mention "our recruitment
team is currently reviewing your profile" in ordinary lowercase prose,
which isn't a signature and shouldn't be touched.
"""

from django.db import migrations

OLD_TEXT = "Recruitment Team"
NEW_TEXT = "Talent Acquisition Team"


def rename_forward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for template in HorillaMailTemplate.objects.all():
        if OLD_TEXT in template.body:
            template.body = template.body.replace(OLD_TEXT, NEW_TEXT)
            template.save(update_fields=["body"])


def rename_backward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for template in HorillaMailTemplate.objects.all():
        if NEW_TEXT in template.body:
            template.body = template.body.replace(NEW_TEXT, OLD_TEXT)
            template.save(update_fields=["body"])


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0007_company_cover_image"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
