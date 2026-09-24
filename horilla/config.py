"""
horilla/config.py

Horilla app configurations
"""

import importlib
import logging

from django.apps import apps
from django.conf import settings
from django.contrib.auth.context_processors import PermWrapper

logger = logging.getLogger(__name__)


def get_apps_in_base_dir():
    return settings.SIDEBARS


def import_method(accessibility):
    module_path, method_name = accessibility.rsplit(".", 1)
    module = __import__(module_path, fromlist=[method_name])
    accessibility_method = getattr(module, method_name)
    return accessibility_method


def sidebar(request):
    """
    Build the sidebar menu list for the requesting user.

    Returns it directly rather than stashing it in module-level state
    keyed by session id - this used to be cached in a plain dict
    (`ALL_MENUS[session_key] = ...`) that was never purged, so every
    session id a long-running process ever saw stayed in memory forever.
    It also recomputed on every single call anyway (get_MENUS is a
    per-request template context processor), so that "cache" never
    actually saved any work - it only leaked. A session-keyed global also
    meant a request with no session yet (session_key is None) shared one
    entry across every such request, which could momentarily leak one
    user's permission-scoped menu into another's response under
    concurrency. Returning the value directly avoids all of that.
    """
    base_dir_apps = get_apps_in_base_dir()
    menus = []

    if not request.user.is_anonymous:
        for app in base_dir_apps:
            if apps.is_installed(app):
                try:
                    sidebar_module = importlib.import_module(app + ".sidebar")

                except Exception as e:
                    logger.error(e)
                    continue

                if sidebar_module:
                    accessibility = None
                    if getattr(sidebar_module, "ACCESSIBILITY", None):
                        accessibility = import_method(sidebar_module.ACCESSIBILITY)

                    if hasattr(sidebar_module, "MENU") and (
                        not accessibility
                        or accessibility(
                            request,
                            sidebar_module.MENU,
                            PermWrapper(request.user),
                        )
                    ):
                        MENU = {}
                        MENU["menu"] = sidebar_module.MENU
                        MENU["app"] = app
                        MENU["img_src"] = sidebar_module.IMG_SRC
                        MENU["submenu"] = []
                        menus.append(MENU)
                        for submenu in sidebar_module.SUBMENUS:

                            accessibility = None

                            if submenu.get("accessibility"):
                                accessibility = import_method(submenu["accessibility"])
                            redirect: str = submenu["redirect"]
                            redirect = redirect.split("?")
                            submenu["redirect"] = redirect[0]

                            if not accessibility or accessibility(
                                request,
                                submenu,
                                PermWrapper(request.user),
                            ):
                                MENU["submenu"].append(submenu)
    return menus


def get_MENUS(request):
    return {"sidebar": sidebar(request)}


def load_ldap_settings():
    """
    Fetch LDAP settings dynamically from the database after Django is ready.
    """
    try:
        from django.db import connection

        from horilla_ldap.models import LDAPSettings

        # Ensure DB is ready before querying
        if not connection.introspection.table_names():
            print("Warning: Database is empty. Using default LDAP settings.")
            return settings.DEFAULT_LDAP_CONFIG

        ldap_config = LDAPSettings.objects.first()
        if ldap_config:
            return {
                "LDAP_SERVER": ldap_config.ldap_server,
                "BIND_DN": ldap_config.bind_dn,
                "BIND_PASSWORD": ldap_config.bind_password,
                "BASE_DN": ldap_config.base_dn,
            }
    except Exception as e:
        print(f"Warning: Could not load LDAP settings ({e})")
        return settings.DEFAULT_LDAP_CONFIG  # Return default on error

    return settings.DEFAULT_LDAP_CONFIG  # Fallback in case of an issue
