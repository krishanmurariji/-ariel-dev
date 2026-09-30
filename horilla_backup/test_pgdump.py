"""
M-1: dump_postgres_db() used to set PGPASSWORD on the whole process's
os.environ, then delete it when done - shared, mutable, process-wide state.
Two backups running close together (the scheduled one firing while a manual
one from the Settings page is still mid-dump, say) could overwrite or clear
each other's password out from under the other's already-running pg_dump
call. The password is now passed through the one subprocess's own
environment instead, so it can never leak between concurrent calls or be
visible to any other code reading os.environ.
"""

import os
import subprocess
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from horilla_backup.pgdump import dump_postgres_db


class PasswordScopingTests(SimpleTestCase):
    def test_the_password_is_never_written_to_the_real_process_environment(self):
        before = dict(os.environ)
        with patch("subprocess.run") as run:
            run.return_value = None
            dump_postgres_db("db", "user", "out.dump", password="s3cret")
        self.assertNotIn("PGPASSWORD", os.environ)
        self.assertEqual(dict(os.environ), before)

    def test_the_subprocess_receives_the_password_via_its_own_env(self):
        with patch("subprocess.run") as run:
            run.return_value = None
            dump_postgres_db("db", "user", "out.dump", password="s3cret", host="db.example.com", port=6543)
        _, kwargs = run.call_args
        self.assertEqual(kwargs["env"]["PGPASSWORD"], "s3cret")
        # everything else the subprocess needs (PATH, etc.) is still there
        self.assertIn("PATH", kwargs["env"])
        command = run.call_args[0][0]
        self.assertIn("db.example.com", command)
        self.assertIn("6543", command)

    def test_no_password_means_no_env_override_at_all(self):
        with patch("subprocess.run") as run:
            run.return_value = None
            dump_postgres_db("db", "user", "out.dump")
        self.assertIsNone(run.call_args.kwargs["env"])

    def test_two_concurrent_dumps_never_see_each_others_password(self):
        """
        The real regression: with the old os.environ["PGPASSWORD"] = ...
        approach, a second call's assignment (or its cleanup deleting the
        key) could happen while the first call's subprocess.run() was still
        executing on another thread, corrupting what the first pg_dump saw.
        Each call's subprocess.run is stubbed to record the password it
        actually received and sleep briefly, to make that race easy to hit
        if it still existed.
        """
        seen = {}

        def fake_run(cmd, **kwargs):
            time.sleep(0.05)
            seen[cmd[-1]] = (kwargs.get("env") or {}).get("PGPASSWORD")

        with patch("subprocess.run", side_effect=fake_run):
            threads = [
                threading.Thread(
                    target=dump_postgres_db,
                    args=(f"db{i}", "user", "out.dump"),
                    kwargs={"password": f"password-{i}"},
                )
                for i in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        for i in range(10):
            self.assertEqual(seen[f"db{i}"], f"password-{i}")
        self.assertNotIn("PGPASSWORD", os.environ)

    def test_a_failed_dump_still_raises_and_leaves_the_environment_clean(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["pg_dump"], stderr="bad credentials"),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                dump_postgres_db("db", "user", "out.dump", password="s3cret")
        self.assertNotIn("PGPASSWORD", os.environ)
