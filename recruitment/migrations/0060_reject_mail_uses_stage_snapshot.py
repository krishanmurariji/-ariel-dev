"""
The "Application Status - Not Selected" mail's interview/non-interview
split checked instance.stage_id.stage_type - but this automation fires on
RejectedCandidate.save() in a background thread (see
horilla_automations/signals.py's send_mail(), which sleeps 0.1s then
re-fetches the candidate fresh from the DB before rendering). Whenever a
candidate is rejected via the Update Status modal's "Add to Reject"
checkbox, that same request has already moved stage_id to the target
(Rejected) stage before creating the RejectedCandidate row, so by the
time this automation reads stage_id.stage_type, it always sees the
post-move "cancelled" type - never the interview/non-interview stage the
candidate was actually rejected from. Every rejection sent through that
flow got the non-interview wording, regardless of where the candidate
really was.

recruitment/cbv/pipeline.py (CandidateUpdateStatusFormView.form_valid)
and recruitment/cbv/candidates.py (RejectReasonFormView.form_valid) now
both snapshot the candidate's real stage type into a new
Candidate.stage_type_at_rejection field before anything moves or any
mail automation could fire - this migration points the template at that
snapshot instead of the live (and by then unreliable) stage_id.
"""

from django.db import migrations

TITLE = "Application Status - Not Selected"

OLD_BODY = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
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

NEW_BODY = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Dear <strong>{{ instance.name }}</strong>,</p>
{% if instance.stage_type_at_rejection == "interview" %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for interviewing at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your profile has not been selected for the next round.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We appreciate the time and effort you invested in the interview process and wish you all the best in your future endeavors.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You are welcome to apply again after <strong>3 months</strong> should a suitable opening arise that matches your skills and experience.</p>
{% else %}
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for applying for the <strong>{{ instance.job_position_id }}</strong> position at {% if instance.recruitment_id.company_id %}<strong>{{ instance.recruitment_id.company_id }}</strong>{% else %}our company{% endif %}.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">After careful consideration, we regret to inform you that your application has not been successful at this time.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">We sincerely appreciate the time and effort you invested in your application. We encourage you to apply again after <strong>3 months</strong> - we'd be glad to consider you for future opportunities that match your skills and career goals.</p>
{% endif %}"""


def use_snapshot(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=NEW_BODY)


def use_live_stage(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    HorillaMailTemplate.objects.filter(title=TITLE).update(body=OLD_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0059_candidate_stage_type_at_rejection_and_more"),
    ]

    operations = [
        migrations.RunPython(use_snapshot, use_live_stage),
    ]
