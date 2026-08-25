"""
Restyles the "Candidate Portal Login" mail template so it matches the same
branded layout already used by the other recruitment lifecycle mail
templates (Application Received, Candidate Stage Updated, Application
Status - Not Selected, Job Opening Closing Reminder - see 0029), instead of
its older, differently-styled card design.
"""

from django.db import migrations

TITLE = "Candidate Portal Login"

OLD_BODY = (
    "<div style=\"font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f6f9; padding: 24px;\">"
    '<div style="max-width: 640px; margin: auto; background: #ffffff; border-radius: 12px; padding: 28px; '
    'box-shadow: 0 4px 14px rgba(0,0,0,0.08); border: 1px solid #eceff3;">'
    '<h2 style="margin: 0 0 18px 0; color: #1f2937; font-size: 22px;">Candidate Portal Login Details</h2>'
    '<p style="font-size: 14px; color: #374151; line-height: 1.7; margin: 0 0 14px 0;">'
    "Hi {{instance.get_full_name}},</p>"
    '<p style="font-size: 14px; color: #374151; line-height: 1.7; margin: 0 0 16px 0;">'
    "You can track your application status from the candidate portal using the credentials below.</p>"
    '<div style="margin: 18px 0; padding: 16px; background: #f9fafb; border-left: 4px solid hsl(8, 77%, 56%); border-radius: 8px;">'
    '<p style="margin: 0 0 8px 0; font-size: 14px; color: #111827;">'
    "<strong>Portal Link:</strong> "
    '<a href="{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/" target="_blank" '
    'style="color: hsl(8, 77%, 56%); text-decoration: none;">'
    "{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/"
    "</a></p>"
    '<p style="margin: 0 0 8px 0; font-size: 14px; color: #111827;"><strong>Username (Email):</strong> {{instance.email}}</p>'
    '<p style="margin: 0; font-size: 14px; color: #111827;"><strong>Password (Mobile Number):</strong> {{instance.mobile}}</p>'
    "</div>"
    '<p style="font-size: 13px; color: #6b7280; margin: 0 0 18px 0;">'
    "For security, please keep these credentials private.</p>"
    '<hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">'
    '<p style="font-size: 13px; color: #6b7280; margin: 0;">Regards,</p>'
    '<p style="font-size: 13px; color: #111827; margin: 6px 0 0 0;"><strong>{{self.get_full_name}} | {{self.get_department}} | {{self.get_company}}</strong></p>'
    "</div></div>"
)

NEW_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
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
</div>"""


def restyle_forward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=NEW_BODY)


def restyle_backward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=OLD_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0036_candidatedocumentrequest_is_mandatory"),
    ]

    operations = [
        migrations.RunPython(restyle_forward, restyle_backward),
    ]
