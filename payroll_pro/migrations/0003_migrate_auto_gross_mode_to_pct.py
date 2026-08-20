"""
Data migration: convert existing SalaryStructure rows using the removed
"auto" (back out employer PF from CTC) gross-derivation mode to the
equivalent "pct" (Fixed % of Monthly CTC) mode, preserving their current
effective gross exactly so no structure's live payroll math silently
changes just because the "auto" choice is no longer offered in the UI.

Re-implemented here rather than imported from payroll_pro.services, per
Django migration best practice — a later edit to services.py must never
silently change what this migration replays.
"""

from django.db import migrations


def migrate_auto_to_pct(apps, schema_editor):
    SalaryStructure = apps.get_model("payroll_pro", "SalaryStructure")
    for structure in SalaryStructure.objects.filter(gross_mode="auto"):
        basic_pct = 40.0
        basic_comp = structure.components.filter(
            is_basic=True, component_type="pct_gross"
        ).first()
        if basic_comp:
            basic_pct = basic_comp.value or 0.0

        denominator = 1.0
        if structure.include_employer_pf_in_ctc and structure.pf_enabled:
            denominator += (structure.pf_employer_pct or 0.0) / 100.0 * (
                basic_pct / 100.0
            )
        gross_pct = (1.0 / denominator) * 100.0 if denominator else 100.0

        structure.gross_mode = "pct"
        structure.gross_pct = round(gross_pct, 4)
        structure.save(update_fields=["gross_mode", "gross_pct"])


class Migration(migrations.Migration):

    dependencies = [
        ("payroll_pro", "0002_add_gratuity_fields_alter_gross_mode_default"),
    ]

    operations = [
        migrations.RunPython(migrate_auto_to_pct, reverse_code=migrations.RunPython.noop),
    ]
