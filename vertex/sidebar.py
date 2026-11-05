"""
vertex/sidebar.py

Sidebar menu for Ariel Vertex. Each phase appends its own submenus; visibility is
driven by the portal-role capability, not by Django model permissions.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

MENU = _("Vertex")
IMG_SRC = "images/ui/wallet-outline.svg"

SUBMENUS = [
    {
        "menu": _("Roles"),
        "redirect": reverse("vertex-roles"),
        "accessibility": "vertex.sidebar.roles_accessibility",
    },
]


def roles_accessibility(request, submenu, user_perms, *args, **kwargs):
    from vertex.access import has_cap
    from vertex.roles import Cap

    return has_cap(request.user, Cap.ROLES_MANAGE)
