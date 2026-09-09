"""
0022_remap_welcome_furthest_step_key.py

The stepper's STEPS list merges the old "documents" (Document
Verification) and "welcome" (Offer Letter) steps into one step - kept at
the "documents" key/position, now labeled "Offer, Hiring & Closure" and
also covering Background Verification. The "welcome" key no longer
exists in STEP_KEYS.

OnboardingCandidateDetail.furthest_step_key stores a step key, and
stepper._nav_context computes unlocked_index from
STEP_KEYS.index(furthest_step_key) - so any row still holding the
literal value "welcome" would make that lookup raise ValueError.

Every row keyed "documents" needs no change (it's the default, unchanged
position). A row keyed "welcome" had, under the old 8-step list, already
unlocked everything except "finalize" - "employee_checklist" is the new
last non-final step, so remapping to it preserves that same "everything
but Finalize" unlock state under the new 7-step list. Mirrors the same
approach as 0018_swap_furthest_step_key_for_reorder.py.
"""

from django.db import migrations


def remap_welcome_to_employee_checklist(apps, schema_editor):
    OnboardingCandidateDetail = apps.get_model("onboarding", "OnboardingCandidateDetail")
    OnboardingCandidateDetail.objects.filter(furthest_step_key="welcome").update(
        furthest_step_key="employee_checklist"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0021_background_verification_notes"),
    ]

    operations = [
        migrations.RunPython(
            remap_welcome_to_employee_checklist, migrations.RunPython.noop
        ),
    ]
