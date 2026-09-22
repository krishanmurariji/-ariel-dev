"""
Re-saves every existing GoogleApi row once so its (until now plaintext)
google_client_secret and token get encrypted immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    GoogleApi = apps.get_model("gmail_auth", "GoogleApi")
    for api in GoogleApi.objects.all():
        api.save(update_fields=["google_client_secret", "token"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("gmail_auth", "0003_alter_googleapi_google_client_secret_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
