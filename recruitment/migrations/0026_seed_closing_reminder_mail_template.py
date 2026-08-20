from django.db import migrations

MAIL_TEMPLATE_TITLE = "Job Opening Closing Reminder"

MAIL_TEMPLATE_BODY = (
    '<p style="font-size: 12.8px;">Hi,</p>'
    '<p style="font-size: 12.8px;">This is a reminder that the job opening '
    "<strong>{{ instance.title }}</strong> is scheduled to close on "
    "<strong>{{ instance.end_date }}</strong>.</p>"
    '<p style="font-size: 12.8px;">Please review the pipeline and take any '
    "necessary action before it closes.</p>"
    '<p style="font-size: 12.8px;">Regards,<br>{{ instance.company_id }}</p>'
)


def create_mail_template(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.get_or_create(
        title=MAIL_TEMPLATE_TITLE,
        defaults={"body": MAIL_TEMPLATE_BODY},
    )


def delete_mail_template(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=MAIL_TEMPLATE_TITLE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0001_initial"),
        ("recruitment", "0025_recruitment_recruitment_heads"),
    ]

    operations = [
        migrations.RunPython(create_mail_template, delete_mail_template),
    ]
