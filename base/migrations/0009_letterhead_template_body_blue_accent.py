"""
The letterhead wrapper's own accent (header gradient strip, CTA button
color, no-logo text fallback - see base/mail_letterhead.py's
LETTERHEAD_ACCENT) switched from orange (#E54F38) to blue (#0B5FCE), but
several HorillaMailTemplate bodies - and any MailAutomation with a
freeform body instead of a linked template - still hardcode the old
orange as a literal hex value directly in their stored HTML (border/link/
button colors, plus the matching light-orange highlight-box tint), since
that color was typed into the template body itself rather than read from
the shared accent. Those mails were still showing an orange highlight
box/button sitting under the new blue header, rather than the letterhead
now being blue throughout.

Scans every stored body for both known apps a mail body can live in
(HorillaMailTemplate.body, MailAutomation.mail_details) rather than a
fixed list of known titles, so it also catches any admin-authored custom
automation with the same leftover orange - not just the ones this
project's own migrations created.
"""

from django.db import migrations

COLOR_REPLACEMENTS = [
    ("#e54f38", "#0b5fce"),  # accent (borders, links, buttons)
    ("#fff6f4", "#eef5fd"),  # matching light highlight-box tint
]


def _recolor(text):
    changed = False
    for old, new in COLOR_REPLACEMENTS:
        if old in text.lower():
            # Case-preserving-enough for this data: these templates only
            # ever use lowercase hex (confirmed against every existing
            # row), so a straight case-sensitive replace is sufficient
            # and avoids a heavier regex for a one-off data fix.
            text = text.replace(old, new).replace(old.upper(), new)
            changed = True
    return text, changed


def recolor_forward(apps, schema_editor):
    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for template in HorillaMailTemplate.objects.all():
        new_body, changed = _recolor(template.body)
        if changed:
            template.body = new_body
            template.save(update_fields=["body"])

    MailAutomation = apps.get_model("horilla_automations", "MailAutomation")
    for automation in MailAutomation.objects.all():
        if not automation.mail_details:
            continue
        new_details, changed = _recolor(automation.mail_details)
        if changed:
            automation.mail_details = new_details
            automation.save(update_fields=["mail_details"])


def recolor_backward(apps, schema_editor):
    reversed_pairs = [(new, old) for old, new in COLOR_REPLACEMENTS]

    HorillaMailTemplate = apps.get_model("base", "HorillaMailTemplate")
    for template in HorillaMailTemplate.objects.all():
        body = template.body
        changed = False
        for new, old in reversed_pairs:
            if new in body.lower():
                body = body.replace(new, old).replace(new.upper(), old)
                changed = True
        if changed:
            template.body = body
            template.save(update_fields=["body"])

    MailAutomation = apps.get_model("horilla_automations", "MailAutomation")
    for automation in MailAutomation.objects.all():
        if not automation.mail_details:
            continue
        details = automation.mail_details
        changed = False
        for new, old in reversed_pairs:
            if new in details.lower():
                details = details.replace(new, old).replace(new.upper(), old)
                changed = True
        if changed:
            automation.mail_details = details
            automation.save(update_fields=["mail_details"])


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0008_rename_recruitment_team_signature"),
        ("horilla_automations", "0003_alter_mailautomation_mail_details_and_more"),
    ]

    operations = [
        migrations.RunPython(recolor_forward, recolor_backward),
    ]
