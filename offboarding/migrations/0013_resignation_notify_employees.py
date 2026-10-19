from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employee", "0015_manager_of_manager_setting"),
        ("offboarding", "0012_exit_survey_send_timing"),
    ]

    operations = [
        migrations.AddField(
            model_name="resignationletter",
            name="notify_employees",
            field=models.ManyToManyField(
                blank=True,
                related_name="resignations_to_notify",
                to="employee.employee",
                verbose_name="Persons to notify",
            ),
        ),
    ]
