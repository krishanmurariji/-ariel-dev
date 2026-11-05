"""
vertex/sidebar.py

Sidebar menu for Ariel Vertex. Each phase appends submenus; visibility follows the
ordinary Ariel HR permissions (superusers always see everything).
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

MENU = _("Vertex")
IMG_SRC = "images/ui/grid.svg"

SUBMENUS = [
    {
        "menu": _("Projects"),
        "redirect": reverse("vertex-projects"),
        "accessibility": "vertex.sidebar.projects_accessibility",
    },
]


def projects_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_project")
