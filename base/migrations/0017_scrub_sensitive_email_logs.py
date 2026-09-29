"""
base/migrations/0017_scrub_sensitive_email_logs.py

One-time codes and password-reset links used to be stored in the mail log in
plain text (see base/email_log_safety.py). New mails are now stored with the
body hidden; this replaces the body of the ones already stored. Only the body
of matching rows changes - subject, recipient, status and date are kept.
"""

from django.db import migrations

from base.email_log_safety import HIDDEN_BODY, looks_sensitive


def scrub(apps, schema_editor):
    EmailLog = apps.get_model("base", "EmailLog")
    for log in EmailLog.objects.exclude(body=HIDDEN_BODY).iterator():
        if looks_sensitive(log.subject, log.body):
            EmailLog.objects.filter(pk=log.pk).update(body=HIDDEN_BODY)


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0016_create_cache_table"),
    ]

    operations = [
        # The original text cannot be brought back, by design.
        migrations.RunPython(scrub, migrations.RunPython.noop),
    ]
