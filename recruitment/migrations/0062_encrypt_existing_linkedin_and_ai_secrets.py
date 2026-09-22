"""
Re-saves every existing LinkedInAccount/AISettings row once so their
(until now plaintext) client secret, API token, and OpenAI key get
encrypted immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    LinkedInAccount = apps.get_model("recruitment", "LinkedInAccount")
    for account in LinkedInAccount.objects.all():
        account.save(update_fields=["client_secret", "api_token"])

    AISettings = apps.get_model("recruitment", "AISettings")
    for row in AISettings.objects.all():
        row.save(update_fields=["openai_api_key"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0061_alter_aisettings_openai_api_key_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
