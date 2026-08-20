"""
Data migration: before the legacy `Candidate.source` field is dropped
(next migration), fold its two real-world values ("software" = manually
added via the internal Add Candidate form, "email" = created by the
email-intake pipeline) into Platform records, so existing candidates keep
that provenance under the new single "Source" (Platform-backed) field.

"application"/"other" are included too in case any historical data used
them, even though in practice nothing in the app sets those values.
"""

from django.db import migrations

SOURCE_TO_PLATFORM_TITLE = {
    "software": "Manual Entry",
    "email": "Email Intake",
    "application": "Application Form",
    "other": "Other",
}


def backfill_source_into_platform(apps, schema_editor):
    Candidate = apps.get_model("recruitment", "Candidate")
    Platform = apps.get_model("recruitment", "Platform")

    platform_cache = {}
    for source_value, platform_title in SOURCE_TO_PLATFORM_TITLE.items():
        candidates = Candidate.objects.filter(platform__isnull=True, source=source_value)
        if not candidates.exists():
            continue
        if platform_title not in platform_cache:
            platform, _created = Platform.objects.get_or_create(
                title__iexact=platform_title, defaults={"title": platform_title}
            )
            platform_cache[platform_title] = platform
        candidates.update(platform=platform_cache[platform_title])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0017_recruitmentgeneralsetting_default_meeting_provider"),
    ]

    operations = [
        migrations.RunPython(backfill_source_into_platform, noop_reverse),
    ]
