from django.db import migrations


def mark_existing_as_seen(apps, schema_editor):
    """
    seen_by_candidate defaults to False for the column just added, which
    would otherwise make every pre-existing message look "unread" the
    first time a candidate opens the Chat tab. They've had these messages
    in view all along, so backfill them as already-seen; only messages
    sent from here on participate in real unread tracking.
    """
    StageNote = apps.get_model("recruitment", "StageNote")
    StageNote.objects.filter(seen_by_candidate=False).update(seen_by_candidate=True)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0044_stagenote_seen_by_candidate"),
    ]

    operations = [
        migrations.RunPython(mark_existing_as_seen, migrations.RunPython.noop),
    ]
