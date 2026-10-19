from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0063_alter_candidate_referral"),
    ]

    operations = [
        migrations.AddField(
            model_name="interviewschedule",
            name="interview_end_time",
            field=models.TimeField(
                blank=True,
                help_text="Optional. Leave empty to keep the default meeting length.",
                null=True,
                verbose_name="Interview End Time",
            ),
        ),
    ]
