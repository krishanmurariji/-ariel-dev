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
    {
        "menu": _("Status Updates"),
        "redirect": reverse("vertex-status"),
        "accessibility": "vertex.sidebar.status_accessibility",
    },
    {
        "menu": _("Hiring Requests"),
        "redirect": reverse("vertex-hiring"),
        "accessibility": "vertex.sidebar.hiring_accessibility",
    },
    {
        "menu": _("Resource Visibility"),
        "redirect": reverse("vertex-resources"),
        "accessibility": "vertex.sidebar.resources_accessibility",
    },
    {
        "menu": _("Reviews"),
        "redirect": reverse("vertex-reviews"),
        "accessibility": "vertex.sidebar.reviews_accessibility",
    },
    {
        "menu": _("Meeting Minutes"),
        "redirect": reverse("vertex-minutes"),
        "accessibility": "vertex.sidebar.minutes_accessibility",
    },
    {
        "menu": _("Feedback"),
        "redirect": reverse("vertex-feedback"),
        "accessibility": "vertex.sidebar.feedback_accessibility",
    },
    {
        "menu": _("Performance Reports"),
        "redirect": reverse("vertex-performance"),
        "accessibility": "vertex.sidebar.performance_accessibility",
    },
    {
        "menu": _("Improvement Plans"),
        "redirect": reverse("vertex-pip"),
        "accessibility": "vertex.sidebar.pip_accessibility",
    },
    {
        "menu": _("My Performance"),
        "redirect": reverse("vertex-my-performance"),
        "accessibility": "vertex.sidebar.my_performance_accessibility",
    },
]


def projects_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_project")


def reviews_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_reviewrequest")


def minutes_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_meetingminute")


def status_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_statusupdate")


def hiring_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_resourcerequest")


def resources_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_resourcevisibility")


def feedback_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_feedback")


def performance_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_performancereport")


def pip_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_pip")


def my_performance_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("vertex.view_myperformance")
