"""
The "Application Status - Not Selected" mail has two wordings depending on
whether the candidate was rejected while in an "interview"-type stage
(recruitment/migrations/0047, restyled in 0048). The non-interview branch
tells the candidate they're welcome to re-apply after 3 months; the
interview branch never got that line added. This adds it there too, so
every rejected candidate gets the same re-apply window regardless of how
far they got.
"""

from django.db import migrations

TITLE = "Application Status - Not Selected"

OLD_BODY = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
{% if instance.stage_id.stage_type == "interview" %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for interviewing at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your profile has not been selected for the next round.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We appreciate the time and effort you invested in the interview process and wish you all the best in your future endeavors.</p>
{% else %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
{% endif %}"""

NEW_BODY = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
{% if instance.stage_id.stage_type == "interview" %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for interviewing at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your profile has not been selected for the next round.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We appreciate the time and effort you invested in the interview process and wish you all the best in your future endeavors.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You are welcome to apply again after <strong>3 months</strong> should a suitable opening arise that matches your skills and experience.</p>
{% else %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
{% endif %}"""


def add_reapply_line(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=NEW_BODY)


def remove_reapply_line(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=OLD_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0056_interviewschedule_optional_interviewers_and_more"),
    ]

    operations = [
        migrations.RunPython(add_reapply_line, remove_reapply_line),
    ]
