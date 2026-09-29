from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("outlook_auth", "0006_encrypt_existing_azureapi_secrets"),
    ]

    operations = [
        migrations.AddField(
            model_name="azureapi",
            name="reauth_required",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="azureapi",
            name="last_error",
            field=models.CharField(blank=True, default="", editable=False, max_length=500),
        ),
        migrations.AddField(
            model_name="azureapi",
            name="last_error_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
