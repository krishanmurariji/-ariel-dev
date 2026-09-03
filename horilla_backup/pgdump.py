import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def dump_postgres_db(
    db_name, username, output_file, password=None, host="localhost", port=5432
):
    # Set environment variable for the password if provided
    if password:
        os.environ["PGPASSWORD"] = password

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
        subprocess.run(dump_command, check=True, text=True, capture_output=True)
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
    finally:
        # Clean up the environment variable
        if password:
            del os.environ["PGPASSWORD"]
