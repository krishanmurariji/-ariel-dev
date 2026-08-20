"""
Restyles the recruitment lifecycle mail templates (Application Received,
Candidate Stage Updated, Application Status - Not Selected, Job Opening
Closing Reminder) so they share one consistent, branded HTML layout with a
common header and footer, instead of each looking like a different ad-hoc
email. Also adds a "you're welcome to re-apply after 3 months" line to the
rejection mail.
"""

from django.db import migrations

APPLICATION_RECEIVED_TITLE = "Application Received Template"
APPLICATION_RECEIVED_OLD_BODY = (
    "<p>Dear {{ instance.name }},</p>\n\n"
    "<p>Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at \n"
    "{% if instance.recruitment_id.company_id %}\n"
    "<strong>{{ instance.recruitment_id.company_id }}</strong>.\n"
    "{% else %}\n"
    "our company.\n"
    "{% endif %}</p>\n\n"
    "<p>We have received your application and our recruitment team is currently reviewing your profile.</p>\n\n"
    "<p>If your qualifications align with our current requirements, we will contact you for the next steps in the hiring process.</p>\n\n"
    '<p>\nYou can check your application and interview status anytime through our <strong>Candidate Portal</strong>:<br>\n'
    '<a href="{{ request.scheme }}://{{ request.get_host }}{% url \'candidate-login\' %}">{{ request.scheme }}://{{ request.get_host }}{% url \'candidate-login\' %}</a><br><br>\n'
    "Log in using your registered <strong>email address(username)</strong> and <strong>phone number</strong>.\n</p>\n\n"
    "<p>We appreciate your interest in joining our team and taking the time to apply.</p>\n\n"
    "<p>Best regards,<br>\n"
    "{% if instance.recruitment_id.company_id %}\n"
    "<strong>{{ instance.recruitment_id.company_id }}</strong><br>\n"
    "{% endif %}\n"
    "Recruitment Team</p>\n\n"
    "<hr>\n\n"
    "<p>\nThis is an automated message from the HRMS system.\n</p>"
)
APPLICATION_RECEIVED_NEW_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Application Received</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We have received your application and our recruitment team is currently reviewing your profile. If your qualifications align with our current requirements, we will reach out to you with the next steps.</p>
      <div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
        <p style="margin:0 0 12px;font-size:13px;color:#555555;">Track your application and interview status anytime through our Candidate Portal.</p>
        <a href="{{ request.scheme }}://{{ request.get_host }}{% url 'candidate-login' %}" style="display:inline-block;padding:10px 20px;background-color:#e54f38;color:#ffffff;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;">Open Candidate Portal</a>
        <p style="margin:12px 0 0;font-size:12px;color:#888888;">Log in using your registered email address and phone number.</p>
      </div>
      <p style="margin:0;font-size:14px;color:#444444;line-height:1.7;">We appreciate your interest in joining our team and thank you for taking the time to apply.</p>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">{% if instance.recruitment_id.company_id %}<strong style="color:#555555;">{{ instance.recruitment_id.company_id }}</strong> Recruitment Team{% else %}Recruitment Team{% endif %}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>"""

STAGE_UPDATED_TITLE = "Candidate Stage Updated"
STAGE_UPDATED_OLD_BODY = (
    "<p>Hi,</p><p><br></p><p>This is to inform you that a candidate has been moved to your recruitment stage for further evaluation.</p><p><br></p>"
    "<p>Candidate Details:</p><p><br></p>"
    "<p>Candidate Name: {{ instance.name }}</p><p>Job Position: {{ instance.job_position_id }}</p><p>Current Stage: {{ instance.stage_id }}</p><p>Recruitment: {{ instance.recruitment_id }}</p><p><br></p>"
    '<p>Best regards,<br>{% if instance.recruitment_id.company_id %} <span style="">{{ instance.recruitment_id.company_id }}</span><br>{% endif %} Recruitment Team</p>'
    "<hr><p>This is an automated message from the HRMS system.</p>"
)
STAGE_UPDATED_NEW_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Your Application Has Moved Forward</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We wanted to let you know that your application for the <strong>{{ instance.job_position_id }}</strong> position {% if instance.recruitment_id.company_id %}at <strong>{{ instance.recruitment_id.company_id }}</strong> {% endif %}has moved to a new stage in our recruitment process.</p>
      <div style="margin:24px 0;padding:20px;background:#f5f5f5;border-radius:8px;">
        <p style="margin:0 0 8px;font-size:13px;color:#555555;"><strong>Current Stage:</strong> {{ instance.stage_id }}</p>
        <p style="margin:0;font-size:13px;color:#555555;"><strong>Recruitment:</strong> {{ instance.recruitment_id }}</p>
      </div>
      <p style="margin:0;font-size:14px;color:#444444;line-height:1.7;">We'll continue to keep you updated as your application progresses. Thank you for your patience throughout the process.</p>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">{% if instance.recruitment_id.company_id %}<strong style="color:#555555;">{{ instance.recruitment_id.company_id }}</strong> Recruitment Team{% else %}Recruitment Team{% endif %}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>"""

REJECTED_TITLE = "Application Status - Not Selected"
REJECTED_OLD_BODY = (
    '<div style="font-family: \'Segoe UI\', Arial, sans-serif; background-color: #f4f6f9; padding: 40px;">\n'
    '    <div style="max-width: 650px; margin: auto; background: #ffffff; border-radius: 16px; padding: 40px; box-shadow: 0 6px 18px rgba(0,0,0,0.1); border: 1px solid #eaeaea;">\n\n'
    '        <h1 style="color: #e74c3c; text-align: center; font-size: 26px; margin-bottom: 30px; font-weight: 600;">\n'
    "            Application Status Update - {{ instance.job_position_id }}\n"
    "        </h1>\n\n"
    '        <p style="font-size: 16px; color: #333; line-height: 1.8; margin-bottom: 20px;">\n'
    "            Dear <strong>{{ instance.name }}</strong>,\n"
    "        </p>\n\n"
    '        <p style="font-size: 15px; color: #444; line-height: 1.8; margin-bottom: 20px;">\n'
    "            Thank you for applying for the\n"
    "            <strong>{{ instance.job_position_id }}</strong> position at\n"
    "            {% if instance.recruitment_id.company_id %}\n"
    "                <strong>{{ instance.recruitment_id.company_id }}</strong>.\n"
    "            {% else %}\n"
    "                our company.\n"
    "            {% endif %}\n"
    "        </p>\n\n"
    "        {% if instance.rejected_candidate.reject_reason_id %}\n"
    '            <p style="font-size: 15px; color: #444; line-height: 1.8; margin-bottom: 20px;">\n'
    "                After careful consideration, we regret to inform you that your application has not been successful at this time.\n"
    "            </p>\n\n"
    '            <div style="margin: 25px 0; padding: 20px; background: #fff5f5; border-left: 5px solid #e74c3c; border-radius: 8px;">\n'
    '                <p style="margin: 0 0 10px 0; font-size: 15px; color: #b71c1c; font-weight: 600;">\n'
    "                    Reason for Rejection:\n"
    "                </p>\n"
    '                <ul style="margin: 0; padding-left: 22px; font-size: 14px; color: #333; line-height: 1.6;">\n'
    "                    {% if instance.rejected_candidate.reject_reason_id.all %}\n"
    "                        {% for reason in instance.rejected_candidate.reject_reason_id.all %}\n"
    "                            <li>{{ reason }}</li>\n"
    "                        {% endfor %}\n"
    "                    {% else %}\n"
    "                        <li>Not specified.</li>\n"
    "                    {% endif %}\n"
    "                </ul>\n"
    '                <p style="margin-top: 12px; font-size: 14px; color: #555;">\n'
    "                    {{ instance.rejected_candidate.description }}\n"
    "                </p>\n"
    "            </div>\n"
    "        {% endif %}\n\n"
    '        <p style="font-size: 15px; color: #444; line-height: 1.8; margin-top: 20px;">\n'
    "            We sincerely appreciate the time and effort you invested in your application and encourage you to apply for\n"
    "            future opportunities that align with your skills and career goals.\n"
    "        </p>\n\n"
    '        <hr style="margin: 35px 0; border: none; border-top: 1px solid #eee;">\n\n'
    '        <p style="font-size: 13px; color: #777; text-align: center; line-height: 1.6;">\n'
    "            Best regards,<br>\n"
    "            {% if instance.recruitment_id.company_id %}\n"
    '                <strong style="color:#333;">{{ instance.recruitment_id.company_id }}</strong> Recruitment Team\n'
    "            {% else %}\n"
    "                Recruitment Team\n"
    "            {% endif %}\n"
    "        </p>\n\n"
    '        <p style="font-size: 11px; color: #aaa; text-align: center; margin-top: 20px; line-height: 1.4;">\n'
    "            This is an automated email. Please do not reply directly to this message.\n"
    "        </p>\n"
    "    </div>\n"
    "</div>"
)
REJECTED_NEW_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Application Status Update</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
      {% if instance.rejected_candidate.reject_reason_id %}
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
      <div style="margin:20px 0;padding:18px 20px;background:#fff5f5;border-left:4px solid #e54f38;border-radius:8px;">
        <p style="margin:0 0 8px;font-size:13px;color:#b71c1c;font-weight:600;">Reason for Rejection</p>
        <ul style="margin:0;padding-left:20px;font-size:13px;color:#444444;line-height:1.6;">
          {% if instance.rejected_candidate.reject_reason_id.all %}
            {% for reason in instance.rejected_candidate.reject_reason_id.all %}<li>{{ reason }}</li>{% endfor %}
          {% else %}
            <li>Not specified.</li>
          {% endif %}
        </ul>
        {% if instance.rejected_candidate.description %}<p style="margin:10px 0 0;font-size:13px;color:#555555;">{{ instance.rejected_candidate.description }}</p>{% endif %}
      </div>
      {% endif %}
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">{% if instance.recruitment_id.company_id %}<strong style="color:#555555;">{{ instance.recruitment_id.company_id }}</strong> Recruitment Team{% else %}Recruitment Team{% endif %}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>"""

CLOSING_REMINDER_TITLE = "Job Opening Closing Reminder"
CLOSING_REMINDER_OLD_BODY = (
    "<p>Hi,</p><p>This is a reminder that the job opening <strong>{{ instance.title }}</strong> is scheduled to close on <strong>{{ instance.end_date }}</strong>.</p>"
    "<p>Please review the pipeline and take any necessary action before it closes.</p>"
    "<p>Regards,<br>{{ instance.company_id }}</p>"
)
CLOSING_REMINDER_NEW_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
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
</div>"""

TEMPLATES = [
    (APPLICATION_RECEIVED_TITLE, APPLICATION_RECEIVED_OLD_BODY, APPLICATION_RECEIVED_NEW_BODY),
    (STAGE_UPDATED_TITLE, STAGE_UPDATED_OLD_BODY, STAGE_UPDATED_NEW_BODY),
    (REJECTED_TITLE, REJECTED_OLD_BODY, REJECTED_NEW_BODY),
    (CLOSING_REMINDER_TITLE, CLOSING_REMINDER_OLD_BODY, CLOSING_REMINDER_NEW_BODY),
]


def restyle_forward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for title, _old_body, new_body in TEMPLATES:
        HorillaMailTemplate.objects.filter(title=title).update(body=new_body)


def restyle_backward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for title, old_body, _new_body in TEMPLATES:
        HorillaMailTemplate.objects.filter(title=title).update(body=old_body)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0028_alter_candidatehistorysummary_milestones"),
    ]

    operations = [
        migrations.RunPython(restyle_forward, restyle_backward),
    ]
