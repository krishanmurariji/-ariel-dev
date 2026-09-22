"""
Re-saves every existing WhatsappCredientials row once so its (until now
plaintext) Meta access token and webhook verify token get encrypted
immediately.
"""

from django.db import migrations


def encrypt_existing_rows(apps, schema_editor):
    WhatsappCredientials = apps.get_model("whatsapp", "WhatsappCredientials")
    for row in WhatsappCredientials.objects.all():
        row.save(update_fields=["meta_token", "meta_webhook_token"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0002_alter_whatsappcredientials_meta_token_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_rows, noop),
    ]
