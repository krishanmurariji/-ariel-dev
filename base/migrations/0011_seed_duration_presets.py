from django.db import migrations

PRESETS = [
    ("1 Day", 1, "day"),
    ("2 Days", 2, "day"),
    ("5 Days", 5, "day"),
    ("1 Week", 1, "week"),
    ("2 Weeks", 2, "week"),
    ("1 Month", 1, "month"),
    ("Quarter", 1, "quarter"),
    ("Half Yearly", 1, "half_year"),
    ("Yearly", 1, "year"),
]


def seed_presets(apps, schema_editor):
    DurationPreset = apps.get_model("base", "DurationPreset")
    for label, amount, unit in PRESETS:
        DurationPreset.objects.get_or_create(
            label=label, defaults={"amount": amount, "unit": unit}
        )


def remove_presets(apps, schema_editor):
    DurationPreset = apps.get_model("base", "DurationPreset")
    DurationPreset.objects.filter(label__in=[p[0] for p in PRESETS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0010_durationpreset"),
    ]

    operations = [
        migrations.RunPython(seed_presets, remove_presets),
    ]
