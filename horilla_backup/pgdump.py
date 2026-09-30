import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def dump_postgres_db(
    db_name, username, output_file, password=None, host="localhost", port=5432
):
    # Passed via this subprocess's own env, not os.environ - mutating the
    # whole process's environment let a second backup (the scheduled 02:00
    # run landing mid-way through a manual one, say) overwrite or delete the
    # first one's password out from under it, since both were sharing one
    # process-wide value.
    env = {**os.environ, "PGPASSWORD": password} if password else None

    # Construct the pg_dump command
    dump_command = [
        "pg_dump",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        username,
        "-F",
        "c",  # Custom format
        "-f",
        output_file,
        db_name,
    ]

    try:
        # Execute the pg_dump command
        subprocess.run(dump_command, check=True, text=True, capture_output=True, env=env)
    except subprocess.CalledProcessError as e:
        # Previously swallowed outright - the caller (google_drive_backup)
        # would then try to upload a dump file that was never written,
        # failing with a *different*, more confusing error further down.
        # Logging + re-raising surfaces the real cause (bad credentials,
        # pg_dump not on PATH, etc.) instead of that misleading one.
        logger.error("pg_dump failed for database %s: %s", db_name, e.stderr)
        raise
    except FileNotFoundError:
        logger.error(
            "pg_dump executable not found - is postgresql-client installed "
            "on this server?"
        )
        raise
