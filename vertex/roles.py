"""
vertex/roles.py

Example Vertex roles for Ariel HR: each one is an ordinary Django Group holding standard
permissions, shown and edited in the Groups and Permissions grid. The role matrix tests and
the ``vertex_demo_users`` command both build users from this one definition, so the rules that
are tested are exactly the ones you can set up by hand.

Admin (superuser) needs none of this and passes every check.
"""

# permission codenames of the vertex app, grouped by what they allow
PROJECTS_VIEW = ["view_project"]
PROJECTS_MANAGE = ["add_project", "change_project", "add_projectmember", "delete_projectmember",
                   "add_projectdocument", "delete_projectdocument", "add_projectcall"]
PROJECTS_ALL = ["view_allprojects", "change_allprojects"]

ROLES = {
    "Vertex - HR Manager": [
        *PROJECTS_VIEW, *PROJECTS_MANAGE, *PROJECTS_ALL,
        "view_statusupdate", "view_allstatusupdates",
        "view_resourcerequest", "add_resourcerequest", "change_resourcerequest",
        "view_allresourcerequests", "view_resourcevisibility",
        "view_reviewrequest", "add_reviewrequest", "change_reviewrequest", "view_allreviewrequests",
        "add_projectreview", "add_codereview",
        "view_meetingminute", "add_meetingminute", "change_meetingminute", "delete_meetingminute",
        "view_feedback", "add_feedback", "change_feedback", "view_allfeedback", "change_feedbackapproval",
        "view_performancereport", "add_performancereport", "view_allperformancereports",
        "change_performancepublication",
        "view_pip", "add_pip", "change_pip",
        "view_myperformance",
    ],
    "Vertex - Project Manager": [
        *PROJECTS_VIEW, "change_project", "add_projectmember", "delete_projectmember",
        "add_projectdocument", "delete_projectdocument", "add_projectcall",
        "view_statusupdate", "add_statusupdate",
        "view_resourcerequest", "add_resourcerequest", "view_resourcevisibility",
        "view_reviewrequest", "change_reviewrequest", "add_projectreview",
        "view_meetingminute", "add_meetingminute", "change_meetingminute", "delete_meetingminute",
        "view_feedback", "add_feedback", "change_feedback",
        "view_myperformance",
    ],
    "Vertex - Technical Lead": [
        *PROJECTS_VIEW, "add_projectdocument",
        "view_statusupdate", "add_statusupdate",
        "view_reviewrequest", "add_codereview", "add_projectreview",
        "view_feedback", "add_feedback", "change_feedback",
        "view_myperformance",
    ],
    "Vertex - Developer": [
        *PROJECTS_VIEW, "add_projectdocument",
        "view_statusupdate", "add_statusupdate",
        "view_reviewrequest",
        "view_myperformance",
    ],
    "Vertex - Business Person": [
        *PROJECTS_VIEW, "view_statusupdate",
    ],
    "Vertex - Leadership (read only)": [
        *PROJECTS_VIEW, "view_allprojects", "view_statusupdate", "view_resourcevisibility",
        "view_allreviewrequests", "view_reviewrequest",
    ],
}

# (username, first name, last name, group or None, role on the demo project or None)
DEMO_USERS = [
    ("vx.hr", "Hema", "Rao", "Vertex - HR Manager", None),
    ("vx.pm", "Priya", "Mehta", "Vertex - Project Manager", "project_manager"),
    ("vx.lead", "Tarun", "Lal", "Vertex - Technical Lead", "technical_lead"),
    ("vx.dev", "Dev", "Kumar", "Vertex - Developer", "developer"),
    ("vx.dev2", "Divya", "Nair", "Vertex - Developer", "developer"),
    ("vx.biz", "Bhavna", "Shah", "Vertex - Business Person", "business_person"),
    ("vx.boss", "Lakshmi", "Iyer", "Vertex - Leadership (read only)", None),
    ("vx.none", "Nina", "Nobody", None, "member"),
]
DEMO_PROJECT_CODE = "VXDEMO"
DEMO_PASSWORD = "Vx-Demo-4827!"  # local test accounts only; removed with `vertex_demo_users --remove`


def build_groups():
    """Create or update every role group; returns {name: Group}."""
    from django.contrib.auth.models import Group, Permission

    groups = {}
    for name, codenames in ROLES.items():
        group, _created = Group.objects.get_or_create(name=name)
        permissions = Permission.objects.filter(content_type__app_label="vertex", codename__in=codenames)
        missing = set(codenames) - set(permissions.values_list("codename", flat=True))
        if missing:
            raise ValueError(f"{name}: unknown permissions {sorted(missing)}")
        group.permissions.set(permissions)
        groups[name] = group
    return groups
