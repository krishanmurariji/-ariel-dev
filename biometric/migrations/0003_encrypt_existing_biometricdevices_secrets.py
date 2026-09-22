"""
Re-saves every existing BiometricDevices row once so its (until now
plaintext) passwords/keys/tokens get encrypted immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    BiometricDevices = apps.get_model("biometric", "BiometricDevices")
    for device in BiometricDevices.objects.all():
        device.save(
            update_fields=[
                "zk_password",
                "bio_password",
                "api_key",
                "api_secret",
                "api_token",
            ]
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("biometric", "0002_alter_biometricdevices_api_key_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
