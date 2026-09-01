"""
0018_swap_furthest_step_key_for_reorder.py

The stepper's STEPS list swapped the order of its first two steps
("documents" is now index 0, "pre_joining" is now index 1 - previously
the reverse). OnboardingCandidateDetail.furthest_step_key stores a step
key, and stepper._nav_context computes unlocked_index from
STEP_KEYS.index(furthest_step_key) - so any row already holding the
literal value "documents" or "pre_joining" from before this reorder now
means something different (a different, and wrong, set of unlocked
steps) than it did when it was written.

Since only these first two steps changed position, swapping the two
values back into their previously-intended meaning is exact and lossless
- every other stored value (details, post_joining, roles,
employee_checklist, welcome, finalize) keeps the same index before and
after the reorder, so nothing else needs remapping.
"""

from django.db import migrations


def swap_documents_and_pre_joining(apps, schema_editor):
    OnboardingCandidateDetail = apps.get_model("onboarding", "OnboardingCandidateDetail")
    # Use a sentinel so the two updates don't collide with each other.
    OnboardingCandidateDetail.objects.filter(furthest_step_key="pre_joining").update(
        furthest_step_key="__swap_sentinel__"
    )
    OnboardingCandidateDetail.objects.filter(furthest_step_key="documents").update(
        furthest_step_key="pre_joining"
    )
    OnboardingCandidateDetail.objects.filter(furthest_step_key="__swap_sentinel__").update(
        furthest_step_key="documents"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0017_candidatechecklisttask_due_date_and_more"),
    ]

    operations = [
        migrations.RunPython(swap_documents_and_pre_joining, swap_documents_and_pre_joining),
    ]
