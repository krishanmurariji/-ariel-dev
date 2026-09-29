"""
base.py — Main Django settings for Horilla
"""

import os
import sys
from datetime import timedelta
from os.path import join
from pathlib import Path

import environ
from django.contrib.messages import constants as messages
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage

# ========================================
# BASE PATH & ENVIRONMENT CONFIGURATION
# ========================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# settings.py
import os

# No schema defaults for DEBUG/SECRET_KEY/ALLOWED_HOSTS here on purpose -
# see the block below, which gives each its own explicit, considered
# default (or lack of one) instead of environ's generic schema mechanism.
env = environ.Env(
    CSRF_TRUSTED_ORIGINS=(list, ["http://localhost:8000"]),
    CORS_ALLOWED_ORIGINS=(list, []),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
)

env.read_env(os.path.join(BASE_DIR, ".env"), overwrite=True)

# ========================================
# CORE DJANGO SETTINGS
# ========================================
# DEBUG, SECRET_KEY and ALLOWED_HOSTS used to fall back to
# (True, "django-insecure-default-key", ["*"]) whenever any of the three
# was missing from .env/the real environment - so a forgotten var, a lost
# .env file, or a fresh clone deployed without one would silently run in
# production with full debug pages, a publicly-known secret key (which
# also derives the credential-encryption key - see horilla/crypto_fields.py)
# and no Host header validation, instead of failing to start (Critical
# finding C-3).
#
# These three are treated differently on purpose, not just "require
# everything":
#   - DEBUG has one universally safe value when unset: False. There's no
#     reason to force an operator to type DEBUG=False explicitly, so it
#     fails *safe* (defaults False) rather than failing loud.
#   - SECRET_KEY and ALLOWED_HOSTS have no safe generic default - any
#     value baked into the code would be shared by every install that
#     forgets to override it, which is exactly the insecure-default
#     problem this replaces. There's nothing safe to fall back to, so
#     they fail *loud*: env() with no default raises ImproperlyConfigured
#     immediately at process startup (before Django even finishes
#     importing settings) if either is missing.
DEBUG = env.bool("DEBUG", default=False)
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# Belt-and-braces: even with ALLOWED_HOSTS set, refuse to start with a
# wildcard/empty host allow-list once DEBUG is off - that combination is
# almost always a leftover dev value that slipped into a prod .env, and
# open ALLOWED_HOSTS defeats Django's own Host-header validation.
if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must list real hostnames when DEBUG=False. "
        "Refusing to start with an empty or wildcard ('*') host "
        "allow-list outside of local development."
    )

CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# corsheaders is installed and in MIDDLEWARE (below) but was never actually
# configured anywhere - django-cors-headers' own defaults for both of
# these are the same empty/False values used here, so this changes
# nothing about current behavior (no cross-origin requests are allowed).
# It's made explicit, env-driven, so a deployment that needs the API
# consumed from a separate origin (e.g. a mobile app or external SPA) can
# opt in via .env without a code change, instead of the middleware being
# silently inert.
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = env("CORS_ALLOW_ALL_ORIGINS")

# Only require HTTPS for these cookies when DEBUG is off - local dev
# (DEBUG=True) serves plain http://localhost, and browsers silently drop
# Secure cookies over http, which would break login there.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

THEME_APP = "horilla_theme"

INSTALLED_APPS = [
    # daphne must be the first app for Channels to transparently take over
    # `manage.py runserver` with an ASGI-aware dev server - without this,
    # runserver stays on Django's default WSGI dev server and websocket
    # connections would fail even though ASGI_APPLICATION is configured.
    "daphne",
    # Default Django apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third-party apps
    "channels",
    "notifications",
    "mathfilters",
    "corsheaders",
    "simple_history",
    "django_filters",
    "widget_tweaks",
    "auditlog",
    "axes",
    "django_apscheduler",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_yasg",
    # Core Horilla apps
    "horilla_auth",
    THEME_APP,
    "base",
    "employee",
    "recruitment",
    "leave",
    "pms",
    "onboarding",
    "asset",
    "attendance",
    "payroll",
    "payroll_pro",
    "accessibility",
    "horilla_audit",
    "horilla_widgets",
    "horilla_crumbs",
    "horilla_documents",
    "horilla_views",
    "horilla_automations",
    "horilla_api",
    "biometric",
    "helpdesk",
    "offboarding",
    "horilla_backup",
    "project",
    "horilla_meet",
    "report",
    "whatsapp",
    "horilla_ldap",
    "horilla_dbtemplate",
    "outlook_auth",
    "gmail_auth",
]

# ========================================
# REST FRAMEWORK CONFIGURATION
# ========================================

REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "PAGE_SIZE": 20,
    # There was previously no rate limiting on any API endpoint at all.
    # Counters are cache-backed (see CACHES["default"] above, now a shared
    # DB table instead of a per-worker LocMemCache), so these are real
    # limits across every gunicorn worker, not per-worker ones.
    # The Trusted* subclasses identify a client by the proxy-set X-Real-IP
    # (via base.methods.get_client_ip) instead of DRF's default of trusting
    # the client-supplied X-Forwarded-For header, which let a caller dodge
    # the limit by sending a different fake value with every request.
    "DEFAULT_THROTTLE_CLASSES": [
        "horilla_api.throttling.TrustedAnonRateThrottle",
        "horilla_api.throttling.TrustedUserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "300/min",
        # Scoped throttle applied only to LoginAPIView (throttle_scope =
        # "login"). Deliberately keyed by IP alone regardless of which
        # username is being tried - django-axes below locks a specific
        # (username, IP) pair after repeated failures, which by design
        # does NOT catch one IP spraying failed guesses across many
        # different usernames (each pair gets its own budget). This scoped
        # throttle catches exactly that gap: it caps total login attempts
        # per IP regardless of username, as a temporary slowdown rather
        # than axes' harder lockout - gentler on a shared office NAT than
        # widening axes' own lockout parameters to IP-only would be.
        "login": "10/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    # Kept at 60 minutes for now, not shortened - LoginAPIView previously
    # never returned "refresh" to the client and no refresh/blacklist
    # endpoint existed at all, so a shorter access lifetime would only
    # have forced whatever external client consumes this API (no caller
    # of it exists anywhere in this codebase, so it's an external
    # integration - most likely a mobile app) to fully re-login more
    # often, for no real security gain. "refresh" is now included in the
    # login response (additive, safe for any existing caller that already
    # ignores unknown keys) and a refresh endpoint + blacklist-on-rotation
    # is wired up below, so the lifetime can be safely shortened later
    # once that external client is confirmed to actually use the refresh
    # flow rather than just the access token.
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

SWAGGER_SETTINGS = {
    "SECURITY_DEFINITIONS": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Enter your Bearer token here",
        },
        "Basic": {"type": "basic", "description": "Basic authentication."},
    },
    "SECURITY": [{"Bearer": []}, {"Basic": []}],
}

APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"

APSCHEDULER_RUN_NOW_TIMEOUT = 25  # Seconds

# ========================================
# MIDDLEWARE
# ========================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Horilla-specific middlewares
    "base.middleware.CompanyMiddleware",
    "base.middleware.ForcePasswordChangeMiddleware",
    "base.middleware.TwoFactorAuthMiddleware",
    "accessibility.middlewares.AccessibilityMiddleware",
    "horilla.horilla_middlewares.MethodNotAllowedMiddleware",
    "horilla.horilla_middlewares.SVGSecurityMiddleware",
    "horilla.horilla_middlewares.MissingParameterMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
    # Must be last - converts the axes_locked_out flag AxesStandaloneBackend
    # sets during authentication into an actual HTTP 403 response, and is
    # also what makes lockouts work for DRF views (which use their own
    # request object AxesStandaloneBackend alone can't see).
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "horilla.urls"

# ========================================
# DATABASE CONFIGURATION
# ========================================
if env("DATABASE_URL", default=None):
    DATABASES = {"default": env.db()}
else:
    DATABASES = {
        "default": {
            "ENGINE": env("DB_ENGINE", default="django.db.backends.sqlite3"),
            "NAME": env("DB_NAME", default=os.path.join(BASE_DIR, "ArielDb.sqlite3")),
            "USER": env("DB_USER", default=""),
            "PASSWORD": env("DB_PASSWORD", default=""),
            "HOST": env("DB_HOST", default=""),
            "PORT": env("DB_PORT", default=""),
            # Only affects `manage.py test` - Django's default sqlite test DB
            # is ":memory:", which is private per-connection, so a
            # background thread opening its own connection (as the
            # automatic AI-scoring signal does) would see an empty database
            # instead of the just-created candidate row. A file-backed test
            # DB is shared across threads/connections like a real DB is.
            "TEST": {"NAME": os.path.join(BASE_DIR, "TestDB_threaded_test.sqlite3")},
        }
    }

# CONN_MAX_AGE is 0 (close the connection at the end of every request), NOT
# a persistent-connection value like 600. This app is served by ASGI
# (gunicorn + uvicorn workers), and under ASGI every request runs in its
# own short-lived thread while Django's connections are per-thread. With
# CONN_MAX_AGE > 0 the connection is deliberately kept open at the end of
# the request "for reuse", but the thread that owns it is gone, so nothing
# ever reuses or closes it - it just sits idle on the database until Python
# happens to garbage-collect it. Measured directly (200 page loads against
# a local Postgres over the real ASGI server): CONN_MAX_AGE=600 left 72
# connections open and idle that were still there 20 seconds later;
# CONN_MAX_AGE=0 left 1. On production this is what steadily ate all 100 of
# the database's connection slots ("remaining connection slots are reserved
# for roles with the SUPERUSER attribute") and took the site down until a
# restart. See also horilla/background_utils.py for the scheduler/thread
# side of the same problem.
#
# setdefault so a DATABASE_URL that already encodes its own conn_max_age (a
# `?conn_max_age=...` query param) isn't overridden. DB_CONN_MAX_AGE lets a
# deployment opt back in to a positive value if that is ever moved off
# ASGI or behind a real connection pool.
DATABASES["default"].setdefault("CONN_MAX_AGE", env.int("DB_CONN_MAX_AGE", default=0))

# Without a connect timeout, opening a connection to an unreachable or
# overloaded database server waits on the operating system's TCP timeout
# (minutes), during which the request - and the worker serving it - just
# hangs. Fail fast instead. Postgres only; sqlite has no network handshake.
if "postgresql" in DATABASES["default"]["ENGINE"]:
    DATABASES["default"].setdefault("OPTIONS", {}).setdefault(
        "connect_timeout", env.int("DB_CONNECT_TIMEOUT", default=10)
    )

# ========================================
# CACHE
# ========================================
# No CACHES setting previously existed anywhere in this project, so every
# cache.get()/cache.set() call was silently falling back to Django's own
# default - LocMemCache, a real, persistent, process-wide cache - with
# nothing actually saying so anywhere. Made explicit here so the behavior
# is visible instead of implicit, and so it can be swapped out for a
# shared backend (e.g. Redis) later without hunting for where "the
# cache" is configured, since currently it isn't, anywhere.
#
# Two aliases, not one, because the "default" cache isn't only used as an
# optional speed-up elsewhere in this codebase - some existing code (poll
# guards, concurrent-trigger guards, login-attempt throttling) uses
# cache.add() as an actual mutual-exclusion lock, where whether the key
# already exists is the entire mechanism, not a nice-to-have. That code
# needs a real, working cache under `manage.py test` same as in
# production, or its own tests (which deliberately exercise "second
# caller is blocked while the first still holds the lock") stop meaning
# anything.
#
# The new recruitment/onboarding dashboard.py caching (cache_dashboard_json)
# is different: purely a performance optimization with no correctness
# dependency on it actually storing anything, so it gets its own
# "dashboard" alias that becomes DummyCache under test - implements the
# full cache API and runs every real code path a request would, it just
# never actually stores anything, so every get() is a genuine miss and
# every test computes its own fresh answer. Without this separate alias,
# a dashboard endpoint's cached response from one test leaked straight
# into the next test's assertions, which set up entirely different data
# and expected an entirely different, freshly-computed answer (found via
# recruitment.tests.test_platform_conversion failing only when run as
# part of the full suite, never in isolation) - and simply switching
# "default" itself to DummyCache to fix that broke the lock-style tests
# instead, the same way it would break the real locks in production.
# DatabaseCache, not LocMemCache: LocMemCache is per-process, and this app
# runs multiple gunicorn workers - each worker would get its own
# independent cache, so a cache.add()-based lock (poll guards, the OTP/
# candidate-login attempt throttles, the django-axes login lockout, DRF
# throttling) only ever worked within whichever single worker happened to
# handle a given request rather than across all of them together. Backed
# by a real DB table (base/migrations/0016_create_cache_table.py) instead
# of Redis, since Redis isn't confirmed running in production - everyone
# already has reliable access to the same Postgres DB.
#
# MAX_ENTRIES is raised well above Django's default of 300 on purpose:
# once the table passes it, DatabaseCache deletes a third of all rows *in
# cache_key order* (not by age) to make room. Every user session adds a
# row (accessibility filter cache), so 300 is easy to exceed, and a cull
# could then delete lockout/attempt counters, DRF throttle counters and
# cache.add() locks at arbitrary moments.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache_default",
        "OPTIONS": {"MAX_ENTRIES": 50000, "CULL_FREQUENCY": 3},
    },
    "dashboard": {
        "BACKEND": (
            "django.core.cache.backends.dummy.DummyCache"
            if "test" in sys.argv
            else "django.core.cache.backends.locmem.LocMemCache"
        ),
    },
}

# ========================================
# STATIC & MEDIA FILES
# ========================================
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media/")

# ========================================
# AUTHENTICATION & SECURITY
# ========================================
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "horilla_auth.HorillaUser"

X_FRAME_OPTIONS = "SAMEORIGIN"

# ========================================
# TEMPLATES
# ========================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Horilla dynamic context processors
                "horilla.config.get_MENUS",
                "base.context_processors.get_companies",
                "base.context_processors.white_labelling_company",
                "base.context_processors.doc_base_url",
                "base.context_processors.resignation_request_enabled",
                "base.context_processors.timerunner_enabled",
                "base.context_processors.intial_notice_period",
                "base.context_processors.check_candidate_self_tracking",
                "base.context_processors.check_candidate_self_tracking_rating",
                "base.context_processors.check_candidate_portal_mail",
                "base.context_processors.check_platform_conversion_widget",
                "base.context_processors.check_default_meeting_provider",
                "base.context_processors.check_default_probation_period",
                "base.context_processors.get_initial_prefix",
                "base.context_processors.biometric_app_exists",
                "base.context_processors.enable_late_come_early_out_tracking",
                "base.context_processors.enable_profile_edit",
                "horilla_crumbs.context_processors.breadcrumbs",
            ],
            "loaders": [
                "horilla_dbtemplate.loaders.Loader",
                (
                    "django.template.loaders.filesystem.Loader",
                    [BASE_DIR / THEME_APP / "templates"],
                ),
                "django.template.loaders.app_directories.Loader",
                ("django.template.loaders.filesystem.Loader", [BASE_DIR / "templates"]),
            ],
        },
    },
]

WSGI_APPLICATION = "horilla.wsgi.application"
ASGI_APPLICATION = "horilla.asgi.application"

# Channel layer for the chat consumer (recruitment/consumers.py). Defaults
# to the in-memory backend, which needs no extra infrastructure and works
# correctly for a single-process dev server (manage.py runserver). It only
# broadcasts within that one process, though - a production deployment
# running more than one worker process (or more than one machine) needs a
# shared backend instead, e.g. `channels_redis.core.RedisChannelLayer`
# pointed at a real Redis instance. Set CHANNEL_LAYER_BACKEND=redis (and
# CHANNEL_LAYER_REDIS_URL) once that's available, rather than editing this.
if env("CHANNEL_LAYER_BACKEND", default="inmemory") == "redis":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [env("CHANNEL_LAYER_REDIS_URL", default="redis://localhost:6379/0")],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }

# ========================================
# INTERNATIONALIZATION
# ========================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# Every uploaded file passes horilla/upload_handler.py first (blocked file types,
# content that does not match the extension, SVG with script, size limit). It
# must stay ahead of Django's own handlers so it sees each file's first bytes.
FILE_UPLOAD_HANDLERS = [
    "horilla.upload_handler.SafeUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]
UPLOAD_MAX_BYTES = env.int("UPLOAD_MAX_BYTES", default=25 * 1024 * 1024)

LANGUAGES = (
    ("en", "English (US)"),
    ("de", "Deutsche"),
    ("es", "Español"),
    ("fr", "Français"),
    ("ar", "عربى"),
    ("pt-br", "Português (Brasil)"),
    ("zh-hans", "Simplified Chinese"),
    ("zh-hant", "Traditional Chinese"),
    ("it", "Italian"),
    ("tr", "Turkish"),
    ("uk", "Українська"),
)

LOCALE_PATHS = [join(BASE_DIR, "horilla", "locale")]

# ========================================
# LOGGING, MESSAGES, OTHER GLOBALS
# ========================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MESSAGE_TAGS = {
    messages.DEBUG: "oh-alert--warning",
    messages.INFO: "oh-alert--info",
    messages.SUCCESS: "oh-alert--success",
    messages.WARNING: "oh-alert--warning",
    messages.ERROR: "oh-alert--danger",
}

LOGIN_URL = "/login"
SIMPLE_HISTORY_REVERT_DISABLED = True

DJANGO_NOTIFICATIONS_CONFIG = {
    "USE_JSONFIELD": True,
    "SOFT_DELETE": True,
    "USE_WATCHED": True,
    "NOTIFICATIONS_STORAGE": "notifications.storage.DatabaseStorage",
    "TEMPLATE": "notifications.html",
}

# ========================================
# HORILLA-SPECIFIC SETTINGS
# ========================================
WHITE_LABELLING = True
NESTED_SUBORDINATE_VISIBILITY = True
TWO_FACTORS_AUTHENTICATION = False

SIDEBARS = [
    "employee",
    "attendance",
    "leave",
    "payroll",
    "payroll_pro",
    "recruitment",
    "onboarding",
    "offboarding",
    "pms",
    "project",
    "asset",
    "helpdesk",
    "report",
]

# Audit logging is opt-in: the horilla_audit app registers models explicitly
# through its registry, driven by AuditModelConfig and a default whitelist
# (Employee, EmployeeWorkInformation, EmployeeBankDetails).
AUDITLOG_INCLUDE_ALL_MODELS = False
AUDITLOG_EXCLUDE_TRACKING_MODELS = (
    # "<app_name>",
    # "<app_name>.<model>"
)

EMAIL_BACKEND = "base.backends.ConfiguredEmailBackend"

# ========================================
# CANDIDATE EMAIL INTAKE (local LLM)
# ========================================
# Base URL of a locally-hosted Ollama server used to parse resumes/emails
# and match candidates to open job positions, without sending candidate
# PII to a third-party API. See recruitment/intake.py.
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_INTAKE_MODEL = env("OLLAMA_INTAKE_MODEL", default="llama3.2:3b")
# CPU-only inference of a real resume + prompt can take well over a minute.
OLLAMA_TIMEOUT = env.int("OLLAMA_TIMEOUT", default=300)

# Reject-before-decode cap on a single email attachment, applied by both
# providers (recruitment/intake_providers/) before any base64 decode or
# PDF parsing - guards against a mislabeled/oversized attachment spiking
# memory/CPU.
INTAKE_MAX_ATTACHMENT_BYTES = env.int(
    "INTAKE_MAX_ATTACHMENT_BYTES", default=15 * 1024 * 1024
)
# Hard ceiling on how many messages one poll of one mailbox will process,
# regardless of how many arrived - bounds a single poll run under burst
# volume. Anything beyond this is picked up on a later poll (see
# recruitment/intake.py's cursor-advancement logic), never silently lost.
INTAKE_MAX_MESSAGES_PER_POLL = env.int("INTAKE_MAX_MESSAGES_PER_POLL", default=200)
# Wall-clock budget for one poll run, in addition to the message-count
# ceiling above - needed because OLLAMA_TIMEOUT (plus one retry) can make
# a single message take up to 2x OLLAMA_TIMEOUT, so a count ceiling alone
# could still let a poll run for hours under LLM slowness.
INTAKE_POLL_MAX_DURATION_SECONDS = env.int(
    "INTAKE_POLL_MAX_DURATION_SECONDS", default=1200
)
# Must comfortably exceed INTAKE_POLL_MAX_DURATION_SECONDS so IntakePollLock
# staleness recovery never preempts a legitimately still-running poll, but
# reliably reclaims a lock abandoned by a crashed worker.
INTAKE_POLL_LOCK_STALE_SECONDS = env.int(
    "INTAKE_POLL_LOCK_STALE_SECONDS", default=3600
)
# Default per-mailbox daily cap on auto-created candidates (overridable
# per CandidateIntakeMailbox via its daily_candidate_cap field) - simple
# abuse brake against a mailbox being flooded with plausible-looking PDFs.
INTAKE_DAILY_CANDIDATE_CAP = env.int("INTAKE_DAILY_CANDIDATE_CAP", default=50)

"""
DB_INIT_PASSWORD: str

The password used for database setup and initialization. This password is a
48-character alphanumeric string generated using a UUID to ensure high entropy and security.
"""
DB_INIT_PASSWORD = env(
    "DB_INIT_PASSWORD", default="d3f6a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d"
)

# ========================================
# PERMISSIONS / CUSTOM LOGIC
# ========================================
NO_PERMISSION_MODALS = [
    "historicalbonuspoint",
    "assetreport",
    "assetdocuments",
    "returnimages",
    "holiday",
    "companyleave",
    "historicalavailableleave",
    "historicalleaverequest",
    "historicalleaveallocationrequest",
    "leaverequestconditionapproval",
    "historicalcompensatoryleaverequest",
    "employeepastleaverestrict",
    "overrideleaverequests",
    "historicalrotatingworktypeassign",
    "employeeshiftday",
    "historicalrotatingshiftassign",
    "historicalworktyperequest",
    "historicalshiftrequest",
    "multipleapprovalmanagers",
    "attachment",
    "announcementview",
    "emaillog",
    "driverviewed",
    "dashboardemployeecharts",
    "attendanceallowedip",
    "tracklatecomeearlyout",
    "historicalcontract",
    "overrideattendance",
    "overrideleaverequest",
    "overrideworkinfo",
    "multiplecondition",
    "historicalpayslip",
    "reimbursementmultipleattachment",
    "workrecord",
    "historicalticket",
    "skill",
    "historicalcandidate",
    "rejectreason",
    "historicalrejectedcandidate",
    "rejectedcandidate",
    "questionordering",
    "recruitmentsurveyordering",
    "recruitmentsurveyanswer",
    "recruitmentgeneralsetting",
    "resume",
    "recruitmentmailtemplate",
    "profileeditfeature",
]

FILE_STORAGE = FileSystemStorage(location="csv_tmp/")

HORILLA_DATE_FORMATS = {
    "DD/MM/YY": "%d/%m/%y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "YYYY/MM/DD": "%Y/%m/%d",
    "MMMM D, YYYY": "%B %d, %Y",
    "DD MMMM, YYYY": "%d %B, %Y",
    "MMM. D, YYYY": "%b. %d, %Y",
    "D MMM. YYYY": "%d %b. %Y",
    "dddd, MMMM D, YYYY": "%A, %B %d, %Y",
}

HORILLA_TIME_FORMATS = {
    "hh:mm A": "%I:%M %p",  # 12-hour format
    "HH:mm": "%H:%M",  # 24-hour format
    "HH:mm:ss.SSSSSS": "%H:%M:%S.%f",  # 24-hour format with seconds and microseconds
}

BIO_DEVICE_THREADS = {}

# One BackgroundScheduler per biometric device (keyed by device id) - see
# biometric/views.py's _start_device_scheduler(). Saving a device's
# schedule form used to always create a brand-new scheduler with no
# reference to any scheduler already polling that device, so saving the
# same device's schedule twice left two (or more, on every further save)
# independent schedulers running forever in parallel. This registry lets
# the new one stop the previous one first.
BIO_DEVICE_SCHEDULERS = {}

DYNAMIC_URL_PATTERNS = []

APP_URLS = [
    "base.urls",
    "employee.urls",
]

APPS = [
    "auth",
    "base",
    "employee",
    "horilla_documents",
    "horilla_automations",
]

# ========================================
# LDAP CONFIGURATION (Default)
# ========================================
AUTH_LDAP_SERVER_URI = env("AUTH_LDAP_SERVER_URI", default="ldap://127.0.0.1:389")
AUTH_LDAP_BIND_DN = env("AUTH_LDAP_BIND_DN", default="cn=admin,dc=horilla,dc=com")
AUTH_LDAP_BIND_PASSWORD = env("AUTH_LDAP_BIND_PASSWORD", default="")

AUTH_LDAP_USER_ATTR_MAP = {
    "first_name": "givenName",
    "last_name": "sn",
    "email": "mail",
}

# Default LDAP settings
DEFAULT_LDAP_CONFIG = {
    "LDAP_SERVER": env("LDAP_SERVER", default="ldap://127.0.0.1:389"),
    "BIND_DN": env("BIND_DN", default="cn=admin,dc=horilla,dc=com"),
    "BIND_PASSWORD": env("BIND_PASSWORD", default=""),
    "BASE_DN": env("BASE_DN", default="ou=users,dc=horilla,dc=com"),
}

AUTHENTICATION_BACKENDS = [
    # Must come first - axes needs to see (and block) an authentication
    # attempt before ModelBackend gets a chance to check the password, or
    # a locked-out account could still authenticate successfully.
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    # "django_auth_ldap.backend.LDAPBackend",
]

AUTH_LDAP_ALWAYS_UPDATE_USER = True

# ========================================
# LOGIN BRUTE-FORCE PROTECTION (django-axes)
# ========================================
# Replaces the old base.signals.Fail2BanMiddleware/log_login_failed, which
# was dead code in practice - it only ever ran if a "security.log" file
# happened to exist (gated on a LOGGING config that was never set up), and
# even then it banned by session key, not username or IP, so a client
# that simply dropped its session cookie got a fresh attempt budget every
# time. Both the web login (base.views.login_user) and the API login
# (horilla_api.api_views.auth.views.LoginAPIView) call Django's own
# authenticate(), which fires the same user_login_failed signal axes
# listens for, so this covers both without touching either view's
# authentication logic.
AXES_HANDLER = "axes.handlers.database.AxesDatabaseHandler"  # DB-backed - no Redis needed
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=30)
# Locks only the specific (username, IP) pair that failed repeatedly, not
# the IP alone - so one person mistyping their password can't lock out
# everyone else on the same office network/NAT. The tradeoff: this does
# NOT cap an attacker spraying failed guesses across many different
# usernames from one IP (each username+IP pair gets its own independent
# budget). Add a second, IP-only entry here
# (AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"], "ip_address"])
# to also close that gap, at the cost of a shared-NAT office being able to
# lock itself out collectively from enough scattered typos.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
# Resolve the client IP with base.methods.get_client_ip: it prefers the
# X-Real-IP header the reverse proxy sets itself (nginx overwrites it with
# $remote_addr, so a client can't spoof it) and falls back to REMOTE_ADDR
# with no proxy in front. Behind the proxy REMOTE_ADDR alone is the
# proxy's own address for every request, which would collapse every
# client into one bucket - a stranger could then lock any user out.
#
# This deliberately does NOT use AXES_IPWARE_META_PRECEDENCE_ORDER: that
# is only read when the optional django-ipware package is installed, and
# it isn't - without it axes silently ignores that setting and uses
# REMOTE_ADDR (found by sending X-Real-IP over a real HTTP request and
# seeing 127.0.0.1 recorded). A callable needs no extra dependency.
AXES_CLIENT_IP_CALLABLE = "base.methods.get_client_ip"
# Disabled under the test suite - AXES_FAILURE_LIMIT would otherwise trip
# on tests that deliberately post several wrong-password attempts in a
# row (e.g. exercising the "invalid credentials" branch of a login view),
# turning an unrelated later test using the same client/IP into a
# confusing, seemingly-unrelated failure.
AXES_ENABLED = "test" not in sys.argv
