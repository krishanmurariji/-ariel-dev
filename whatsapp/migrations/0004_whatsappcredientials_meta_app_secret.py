from django.db import migrations

import horilla.crypto_fields


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0003_encrypt_existing_whatsapp_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappcredientials",
            name="meta_app_secret",
            field=horilla.crypto_fields.EncryptedTextField(
                blank=True,
                default="",
                help_text="Used to check that incoming webhook messages really come from Meta (Meta developer dashboard > App settings > Basic > App secret). Once saved, messages without a valid signature are rejected.",
                verbose_name="App Secret",
            ),
        ),
    ]
