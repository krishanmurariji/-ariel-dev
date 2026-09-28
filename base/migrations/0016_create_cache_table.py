"""
base/migrations/0016_create_cache_table.py

Creates the DB table backing CACHES["default"] (switched from LocMemCache
to django.core.cache.backends.db.DatabaseCache - see horilla/settings/
base.py) as part of the normal migrate flow, so it exists everywhere
migrate already runs: production (scripts/deploy.sh), CI, and every
single `manage.py test` run, which builds its own fresh test database
from migrations and would otherwise never see a table only the separate,
manual `createcachetable` command creates.

LocMemCache is per-process - with multiple gunicorn workers, each one had
its own independent cache, so anything relying on cache.add() as a real
lock (poll guards, the OTP/candidate-login attempt throttles, and the new
django-axes login lockout) only ever worked within whichever single
worker handled a given request. DatabaseCache is one shared table every
worker reads/writes the same way.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias)


def drop_cache_table(apps, schema_editor):
    from django.core.cache import caches

    table_name = caches["default"]._table
    quoted = schema_editor.connection.ops.quote_name(table_name)
    schema_editor.execute(f"DROP TABLE IF EXISTS {quoted}")


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0015_alter_roster_department_and_more"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
