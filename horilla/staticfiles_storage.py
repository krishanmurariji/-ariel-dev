"""
horilla/staticfiles_storage.py

STORAGES["staticfiles"] (see horilla/settings/base.py). This is what
STATICFILES_STORAGE used to ask for - Django 5.1 removed that setting name,
Django 5.2 (the version this project runs) silently ignores it, so the
compressed/content-hashed output it named was never actually being served;
plain, uncompressed, unhashed files were. WhiteNoise's own middleware was
already installed and already unconditionally requires collectstatic to have
been run (it serves only from STATIC_ROOT), and collectstatic already runs
on every deploy (scripts/deploy.sh) and container start (docker/entrypoint.sh)
- so this only makes the deploy pipeline's existing collectstatic step
actually pay off, it does not add a new deploy requirement.

No optional dependency here: whitenoise is a hard requirement already (its
middleware is unconditionally in MIDDLEWARE), so importing this module carries
no more risk than the app already has.
"""

import logging

from whitenoise.storage import CompressedManifestStaticFilesStorage

logger = logging.getLogger(__name__)


class LenientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """
    Compressed (gzip/brotli) + content-hashed filenames, for cache-busting.

    manifest_strict is turned off: if some `{% static %}` reference can't be
    resolved in the manifest at *request* time (a stale reference, a file
    that failed to collect), that one URL falls back to the plain, unhashed
    filename instead of the page around it hard failing.

    post_process is overridden for the separate case manifest_strict does
    not cover: collectstatic itself aborts the *entire* run the moment any
    single file's internal reference (a JS/CSS `url(...)`, a sourcemap
    comment, ...) points at something that doesn't exist on disk - by
    design confirmed in Django's own source (django/contrib/staticfiles/
    management/commands/collectstatic.py: `if isinstance(processed,
    Exception): raise processed`). In practice that is usually a vendored
    third-party bundle referencing its own sourcemap that was never shipped
    with it (e.g. the bundled ionicons build references a .js.map that
    isn't present) - nothing this project's own code can fix, and not a
    reason every other static file should fail to collect. Such a file is
    logged and left un-rewritten (served under its original, unhashed name)
    instead of aborting the run.
    """

    manifest_strict = False

    def url(self, name, force=False):
        # ~176 templates write {% static '/jquery/ajax.js' %} with a leading
        # slash. That was harmless with the plain storage this project used
        # before the hashed one was switched on, but hashing joins the name
        # onto STATIC_ROOT, and an absolute "/jquery/ajax.js" escapes it
        # (SuspiciousFileOperation -> a bare HTTP 400 on /dashboard/ and
        # /login/). Static names are always relative to STATIC_ROOT, so strip
        # the slash here once instead of editing every template.
        if name:
            name = name.lstrip("/")
        return super().url(name, force)

    def post_process(self, *args, **kwargs):
        for name, hashed_name, processed in super().post_process(*args, **kwargs):
            if isinstance(processed, Exception):
                logger.warning(
                    "collectstatic: could not rewrite internal references in "
                    "%r (%s) - serving it under its original name instead. "
                    "This is expected for some vendored third-party bundles "
                    "and is not itself a problem with this project's code.",
                    name,
                    processed,
                )
                yield name, name, False
            else:
                yield name, hashed_name, processed
