"""
Re-saves every existing DynamicEmailConfiguration row once so its (until
now plaintext) SMTP password gets encrypted immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    DynamicEmailConfiguration = apps.get_model("base", "DynamicEmailConfiguration")
    for row in DynamicEmailConfiguration.objects.all():
        row.save(update_fields=["password"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0013_alter_dynamicemailconfiguration_password"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
