"""
horilla/test_storage_config.py

Regression tests for the H-2 fix: DEFAULT_FILE_STORAGE / STATICFILES_STORAGE
were removed in Django 5.1 and silently ignored by Django 5.2 (this
project's pinned version), so the S3/GCS configuration in addons.py and the
compressed static storage in base.py were never actually taking effect -
uploads always landed on local disk and static files were never compressed
or content-hashed, regardless of settings.

Settings are only ever configured once per process, so each check here
spawns a fresh interpreter with its own environment - the same technique
used to prove the DEBUG/SECRET_KEY/ALLOWED_HOSTS startup guard in
base/test_security_regressions.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)

_PROBE = r"""
import os, sys
import environ
environ.Env.read_env = staticmethod(lambda *a, **k: None)  # ignore the real .env
os.environ["DATABASE_URL"] = "sqlite:///x.db"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings")
os.environ.setdefault("SECRET_KEY", "test-key")
os.environ.setdefault("ALLOWED_HOSTS", "example.com")
import django
django.setup()
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.storage import default_storage

print("DEFAULT_BACKEND=" + settings.STORAGES["default"]["BACKEND"])

action = sys.argv[1] if len(sys.argv) > 1 else ""
if action == "resolve_default":
    default_storage.save.__self__  # touch nothing that hits the network
    default_storage._setup() if hasattr(default_storage, "_setup") else None
    print("DEFAULT_CLASS=" + type(default_storage._wrapped).__name__)
elif action == "s3_url":
    print("URL=" + default_storage.url("some/file.pdf"))
    print("QS_AUTH=" + str(default_storage.querystring_auth))
    print("QS_EXPIRE=" + str(default_storage.querystring_expire))
    print("OVERWRITE=" + str(default_storage.file_overwrite))
elif action == "gcs_class":
    try:
        default_storage.bucket_name
    except Exception:
        pass
    print("DEFAULT_CLASS=" + type(default_storage._wrapped).__name__)
elif action == "static_url":
    name = sys.argv[2]
    print("STATIC_URL=" + staticfiles_storage.url(name))
"""


def _run(action, *extra_args, env_overrides=None, expect_ok=True):
    env = {**os.environ}
    env.pop("AWS_ACCESS_KEY_ID", None)
    env.pop("GS_BUCKET_NAME", None)
    env["PYTHONPATH"] = str(BASE_DIR)
    # This test module itself runs under `manage.py test --settings=...
    # test_settings_sqlite` - and passing --settings sets DJANGO_SETTINGS_
    # MODULE as a real process env var, which this subprocess would
    # otherwise inherit, silently testing the wrong (Postgres-avoiding,
    # addons.py-skipping) settings module instead of the real one - forced
    # here rather than setdefault()'d in the probe script.
    env["DJANGO_SETTINGS_MODULE"] = "horilla.settings"
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, action, *extra_args],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        env=env,
        timeout=60,
    )
    if expect_ok and result.returncode != 0:
        raise AssertionError(result.stderr[-3000:])
    return result.stdout


def _field(output, key):
    for line in output.splitlines():
        if line.startswith(key + "="):
            return line[len(key) + 1 :]
    raise AssertionError(f"{key}= not found in output:\n{output}")


class NoBucketConfiguredTests(SimpleTestCase):
    """The state every real deployment of this app is in today."""

    def test_default_storage_is_local_disk(self):
        out = _run("resolve_default")
        self.assertEqual(
            _field(out, "DEFAULT_BACKEND"),
            "django.core.files.storage.FileSystemStorage",
        )
        self.assertEqual(_field(out, "DEFAULT_CLASS"), "FileSystemStorage")


class S3ConfiguredTests(SimpleTestCase):
    def test_aws_keys_switch_default_storage_to_the_private_s3_backend(self):
        out = _run(
            "s3_url",
            env_overrides={
                "AWS_ACCESS_KEY_ID": "AKIAFAKEFAKEFAKEFAKE",
                "AWS_SECRET_ACCESS_KEY": "fake" * 10,
                "AWS_STORAGE_BUCKET_NAME": "test-bucket",
                "AWS_S3_REGION_NAME": "us-east-1",
            },
        )
        self.assertEqual(
            _field(out, "DEFAULT_BACKEND"), "horilla.media_storage_s3.PrivateS3MediaStorage"
        )
        url = _field(out, "URL")
        # A signed link, not a permanently-public one - generated locally,
        # no network call, so a wrong/fake key still proves the config wires
        # through correctly end to end.
        self.assertIn("test-bucket", url)
        self.assertIn("Signature=", url)
        self.assertEqual(_field(out, "QS_AUTH"), "True")
        self.assertEqual(_field(out, "QS_EXPIRE"), "300")
        self.assertEqual(_field(out, "OVERWRITE"), "False")


class GCSConfiguredTests(SimpleTestCase):
    def test_gs_bucket_name_switches_default_storage_to_the_private_gcs_backend(self):
        out = _run("gcs_class", env_overrides={"GS_BUCKET_NAME": "test-bucket"})
        self.assertEqual(
            _field(out, "DEFAULT_BACKEND"), "horilla.media_storage_gcs.PrivateGCSMediaStorage"
        )
        self.assertEqual(_field(out, "DEFAULT_CLASS"), "PrivateGCSMediaStorage")


class StaticFilesManifestTests(SimpleTestCase):
    NAME = "horilla_theme/assets/css/tailwind.css"

    def test_debug_mode_keeps_urls_unhashed_for_easy_local_dev(self):
        # WhiteNoise's own CompressedManifestStaticFilesStorage.url() docs:
        # "Return the non-hashed URL in DEBUG mode." Confirms local dev
        # behaves exactly as before this fix - nothing to regress there.
        out = _run("static_url", self.NAME, env_overrides={"DEBUG": "True"})
        self.assertEqual(_field(out, "STATIC_URL"), f"/static/{self.NAME}")

    def test_production_mode_serves_the_hashed_compressed_name(self):
        manifest_path = BASE_DIR / "staticfiles" / "staticfiles.json"
        if not manifest_path.exists():
            self.skipTest("collectstatic has not been run in this checkout")
        out = _run(
            "static_url",
            self.NAME,
            env_overrides={"DEBUG": "False", "ALLOWED_HOSTS": "example.com"},
        )
        url = _field(out, "STATIC_URL")
        self.assertNotEqual(url, f"/static/{self.NAME}")
        self.assertRegex(url, r"/static/horilla_theme/assets/css/tailwind\.[0-9a-f]{12}\.css")

    def test_collectstatic_does_not_abort_on_a_broken_reference(self):
        """
        Without horilla.staticfiles_storage's post_process override, a
        vendored third-party bundle referencing a sourcemap/image that was
        never shipped with it (nothing to do with this project's own code -
        the bundled ionicons build is one real example) aborts collectstatic
        *entirely*: django/contrib/staticfiles/management/commands/
        collectstatic.py re-raises any exception a storage's post_process()
        yields. Reproduced here with a small isolated static source instead
        of the real project tree, so this test is fast, deterministic, and
        never touches (or fights Windows file-locking on) the real
        staticfiles/ directory.
        """
        import tempfile

        from django.core.management import call_command
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as root_dir:
            source_dir, root_dir = Path(source_dir), Path(root_dir)
            (source_dir / "broken.css").write_text(
                "body { background: url('missing-image-that-was-never-shipped.png'); }"
            )
            with override_settings(STATICFILES_DIRS=[source_dir], STATIC_ROOT=str(root_dir)):
                call_command("collectstatic", "--noinput", verbosity=0)
                manifest = json.loads((root_dir / "staticfiles.json").read_text())
            self.assertIn("broken.css", manifest["paths"])


class DeadStorageFilesAreGoneTests(SimpleTestCase):
    def test_the_broken_monkeypatch_files_were_removed_not_left_as_a_trap(self):
        for relative in (
            "horilla/storage_backends.py",
            "horilla/horilla_backends.py",
            "horilla/horilla_backends_gcp.py",
        ):
            self.assertFalse((BASE_DIR / relative).exists(), relative)

    def test_nothing_still_imports_them(self):
        import subprocess as sp

        result = sp.run(
            ["git", "grep", "-lE", r"storage_backends|horilla_backends"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        hits = [
            line
            for line in result.stdout.splitlines()
            if not line.endswith((".md",)) and "test_storage_config.py" not in line
        ]
        self.assertEqual(hits, [], hits)
