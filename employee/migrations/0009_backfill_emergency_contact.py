"""
Data migration: copies each Employee's existing single
emergency_contact/emergency_contact_name/emergency_contact_relation
values into a first EmployeeEmergencyContact row, so switching the UI to
the new multi-contact model doesn't lose anyone's existing data. The
original three fields are left untouched (other code - reports, exports,
the API - still reads them).
"""

from django.db import migrations


def backfill_emergency_contacts(apps, schema_editor):
    Employee = apps.get_model("employee", "Employee")
    EmployeeEmergencyContact = apps.get_model("employee", "EmployeeEmergencyContact")

    employees = Employee.objects.exclude(
        emergency_contact_name__isnull=True
    ).exclude(emergency_contact_name="")
    rows = [
        EmployeeEmergencyContact(
            employee_id=employee,
            name=employee.emergency_contact_name,
            phone=employee.emergency_contact or "",
            relation=employee.emergency_contact_relation,
        )
        for employee in employees
    ]
    EmployeeEmergencyContact.objects.bulk_create(rows)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("employee", "0008_employeeemergencycontact"),
    ]

    operations = [
        migrations.RunPython(backfill_emergency_contacts, noop_reverse),
    ]
