"""
Re-saves every existing GoogleCloudCredential/GoogleCredential row once so
their (until now plaintext) client secrets and tokens get encrypted
immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    GoogleCloudCredential = apps.get_model("horilla_meet", "GoogleCloudCredential")
    for row in GoogleCloudCredential.objects.all():
        row.save(update_fields=["client_secret"])

    GoogleCredential = apps.get_model("horilla_meet", "GoogleCredential")
    for row in GoogleCredential.objects.all():
        row.save(update_fields=["token", "refresh_token", "client_secret"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("horilla_meet", "0003_alter_googlecloudcredential_client_secret_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
