"""
Re-saves every existing AzureApi row once so its (until now plaintext)
outlook_client_secret and token get encrypted immediately, instead of
waiting for the next time someone edits that mail server or its token
happens to refresh.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    AzureApi = apps.get_model("outlook_auth", "AzureApi")
    for api in AzureApi.objects.all():
        api.save(update_fields=["outlook_client_secret", "token"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("outlook_auth", "0005_alter_azureapi_outlook_client_secret_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
