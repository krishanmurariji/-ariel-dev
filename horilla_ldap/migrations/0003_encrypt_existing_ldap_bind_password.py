"""
Re-saves every existing LDAPSettings row once so its (until now plaintext)
bind_password gets encrypted immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    LDAPSettings = apps.get_model("horilla_ldap", "LDAPSettings")
    for settings_row in LDAPSettings.objects.all():
        settings_row.save(update_fields=["bind_password"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("horilla_ldap", "0002_alter_ldapsettings_bind_password"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
