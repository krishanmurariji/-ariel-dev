"""
Splits the "Application Status - Not Selected" mail body (see 0042) into two
wordings depending on where the candidate was rejected from: candidates who
were rejected while sitting in an "Interview"-type stage get interview-
specific wording ("Thank you for interviewing at ... your profile has not
been selected for the next round ... the interview process"), while every
other candidate keeps the original "application" wording. Header and footer
are untouched.
"""

from django.db import migrations

TITLE = "Application Status - Not Selected"

OLD_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Application Status Update</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">{% if instance.recruitment_id.company_id %}<strong style="color:#555555;">{{ instance.recruitment_id.company_id }}</strong> Recruitment Team{% else %}Recruitment Team{% endif %}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>"""

NEW_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Application Status Update</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
      {% if instance.stage_id.stage_type == "interview" %}
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for interviewing at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your profile has not been selected for the next round.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We appreciate the time and effort you invested in the interview process and wish you all the best in your future endeavors.</p>
      {% else %}
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
      {% endif %}
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">{% if instance.recruitment_id.company_id %}<strong style="color:#555555;">{{ instance.recruitment_id.company_id }}</strong> Recruitment Team{% else %}Recruitment Team{% endif %}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>"""


def restyle_forward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=NEW_BODY)


def restyle_backward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=OLD_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0046_interviewschedule_interview_mode"),
    ]

    operations = [
        migrations.RunPython(restyle_forward, restyle_backward),
    ]
