"""
Strips the card-wrapper/header/footer markup baked directly into each
HorillaMailTemplate body, since horilla_automations/signals.py's send_mail()
now wraps every automation-driven mail body in the shared letterhead
(base/mail_letterhead.py, mail/letterhead_base.html) automatically -
keeping the wrapper in the stored body too would double it up (a card
inside a card). "Job Opening Closing Reminder" is sent via
recruitment/scheduler.py rather than the automation engine, which wraps
it the same way directly in Python, so it needs the same trim.

A couple of details that lived in the old wrapper's header/footer (the
"Application Status Update" headline, the Candidate Portal Login mail's
sender name/department/company signature) move into the body itself,
since the shared letterhead's own header is just a small tag label (no
big title) and its footer is a generic company/team signature (no
per-sender detail).
"""

from django.db import migrations

BODIES = {
    "Job Opening Closing Reminder": {
        "old": """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Job Opening Closing Soon</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">This is a reminder that the job opening <strong>{{ instance.title }}</strong> is scheduled to close on <strong>{{ instance.end_date }}</strong>.</p>
      <div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
        <p style="margin:0;font-size:13px;color:#555555;">Please review the pipeline and take any necessary action, such as advancing, hiring, or closing out remaining candidates, before it closes.</p>
      </div>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;"><strong style="color:#555555;">{{ instance.company_id }}</strong> Recruitment Team</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>""",
        "new": """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">This is a reminder that the job opening <strong>{{ instance.title }}</strong> is scheduled to close on <strong>{{ instance.end_date }}</strong>.</p>
<div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
  <p style="margin:0;font-size:13px;color:#555555;">Please review the pipeline and take any necessary action, such as advancing, hiring, or closing out remaining candidates, before it closes.</p>
</div>""",
    },
    "Application Received Template": {
        "old": """<p>Dear {{ instance.name }},</p>

<p>Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at
{% if instance.recruitment_id.company_id %}
<strong>{{ instance.recruitment_id.company_id }}</strong>.
{% else %}
our company.
{% endif %}</p>

<p>We have received your application and our recruitment team is currently reviewing your profile.</p>

<p>If your qualifications align with our current requirements, we will contact you for the next steps in the hiring process.</p>

<p style="margin:20px 0;padding:16px;background-color:#f5f5f5;border-radius:6px;">
You can check your application and interview status anytime through our <strong>Candidate Portal</strong>:<br>
<a href="{{ request.scheme }}://{{ request.get_host }}{% url 'candidate-login' %}" style="color:#e54f38;font-weight:bold;">{{ request.scheme }}://{{ request.get_host }}{% url 'candidate-login' %}</a><br><br>
Log in using your registered <strong>email address</strong> and <strong>phone number</strong>.
</p>

<p>We appreciate your interest in joining our team and taking the time to apply.</p>

<p>Best regards,<br>
{% if instance.recruitment_id.company_id %}
<strong>{{ instance.recruitment_id.company_id }}</strong><br>
{% endif %}
Recruitment Team</p>

<hr style="border:none;border-top:1px solid #eee;">

<p style="font-size:12px;color:#888;text-align:center;">
This is an automated message from the HRMS system.
</p>""",
        "new": """<p>Dear {{ instance.name }},</p>

<p>Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at
{% if instance.recruitment_id.company_id %}
<strong>{{ instance.recruitment_id.company_id }}</strong>.
{% else %}
our company.
{% endif %}</p>

<p>We have received your application and our recruitment team is currently reviewing your profile.</p>

<p>If your qualifications align with our current requirements, we will contact you for the next steps in the hiring process.</p>

<p style="margin:20px 0;padding:16px;background-color:#f5f5f5;border-radius:6px;">
You can check your application and interview status anytime through our <strong>Candidate Portal</strong>:<br>
<a href="{{ request.scheme }}://{{ request.get_host }}{% url 'candidate-login' %}" style="color:#e54f38;font-weight:bold;">{{ request.scheme }}://{{ request.get_host }}{% url 'candidate-login' %}</a><br><br>
Log in using your registered <strong>email address</strong> and <strong>phone number</strong>.
</p>

<p>We appreciate your interest in joining our team and taking the time to apply.</p>""",
    },
    "Application Status - Not Selected": {
        "old": """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
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
</div>""",
        "new": """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
{% if instance.stage_id.stage_type == "interview" %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for interviewing at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your profile has not been selected for the next round.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We appreciate the time and effort you invested in the interview process and wish you all the best in your future endeavors.</p>
{% else %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
{% endif %}""",
    },
    "Candidate Portal Login": {
        "old": """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Candidate Portal Login Details</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>{{instance.get_full_name}}</strong>,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You can track your application status from the candidate portal using the credentials below.</p>
      <div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
        <p style="margin:0 0 10px;font-size:13px;color:#555555;"><strong>Portal Link:</strong> <a href="{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/" target="_blank" style="color:#e54f38;text-decoration:none;">{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/</a></p>
        <p style="margin:0 0 8px;font-size:13px;color:#555555;"><strong>Username (Email):</strong> {{instance.email}}</p>
        <p style="margin:0;font-size:13px;color:#555555;"><strong>Password (Mobile Number):</strong> {{instance.mobile}}</p>
      </div>
      <p style="margin:0;font-size:13px;color:#888888;">For security, please keep these credentials private.</p>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">Regards,<br><strong style="color:#555555;">{{self.get_full_name}}</strong> | {{self.get_department}} | {{self.get_company}}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>""",
        "new": """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>{{instance.get_full_name}}</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You can track your application status from the candidate portal using the credentials below.</p>
<div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
  <p style="margin:0 0 10px;font-size:13px;color:#555555;"><strong>Portal Link:</strong> <a href="{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/" target="_blank" style="color:#e54f38;text-decoration:none;">{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/</a></p>
  <p style="margin:0 0 8px;font-size:13px;color:#555555;"><strong>Username (Email):</strong> {{instance.email}}</p>
  <p style="margin:0;font-size:13px;color:#555555;"><strong>Password (Mobile Number):</strong> {{instance.mobile}}</p>
</div>
<p style="margin:0 0 16px;font-size:13px;color:#888888;">For security, please keep these credentials private.</p>
<p style="margin:0;font-size:13px;color:#777777;">Regards,<br><strong>{{self.get_full_name}}</strong> | {{self.get_department}} | {{self.get_company}}</p>""",
    },
}


def strip_forward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for title, bodies in BODIES.items():
        HorillaMailTemplate.objects.filter(title=title).update(body=bodies["new"])


def strip_backward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for title, bodies in BODIES.items():
        HorillaMailTemplate.objects.filter(title=title).update(body=bodies["old"])


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0047_interview_stage_rejection_mail_wording"),
    ]

    operations = [
        migrations.RunPython(strip_forward, strip_backward),
    ]
