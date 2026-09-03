"""
onboarding/self_onboarding_steps.py

Single source of truth for the candidate self-onboarding wizard's step
order, labels, and URL names - used by every step's view (to build the
sidebar) and by the base template (to render it). Previously each of the
six step templates hard-coded its own copy of this list, so adding,
removing, or reordering a step meant editing markup in every file.

Bank Details was removed as a step (data collection moved to the
HR-facing Employee "Bank Info" tab, employee/forms.py). Education and
Experience - formerly two formsets sharing the "Personal Details" page -
are now their own steps.
"""

from django.utils.translation import gettext_lazy as _

STEPS = [
    {"key": "account", "label": _("Account Setup"), "url_name": "user-creation", "icon": "lock-closed-outline"},
    {"key": "profile", "label": _("Profile"), "url_name": "profile-view", "icon": "person-outline"},
    {"key": "roles", "label": _("Roles & Responsibility"), "url_name": "roles-responsibility", "icon": "document-text-outline"},
    {"key": "documents", "label": _("Documents"), "url_name": "document-upload", "icon": "cloud-upload-outline"},
    {"key": "personal", "label": _("Personal Details"), "url_name": "employee-creation", "icon": "id-card-outline"},
    {"key": "education", "label": _("Education"), "url_name": "employee-education", "icon": "school-outline"},
    {"key": "experience", "label": _("Experience"), "url_name": "employee-experience", "icon": "briefcase-outline"},
]

STEP_KEYS = [step["key"] for step in STEPS]


def step_index(key):
    """0-based position of a step key, for computing done/current/upcoming."""
    return STEP_KEYS.index(key)


def next_step_url_name(key):
    """URL name of the step after `key`, or None if `key` is the last step."""
    idx = step_index(key)
    if idx + 1 < len(STEPS):
        return STEPS[idx + 1]["url_name"]
    return None


def sidebar_context(current_key, company, portal_count=None):
    """
    The context every self-onboarding step template needs to render
    self_onboarding_base.html's left-rail stepper and mobile progress bar -
    built once here so a step's view only has to merge this in, rather
    than each hand-computing "am I done/current/upcoming" per step.

    `portal_count` is OnboardingPortal.count - how many steps this
    candidate has actually completed. It's what decides the checkmarks,
    kept separate from `current_key` (the page actually being viewed)
    because step order here is only soft-enforced (see
    onboarding.views._resume_redirect) - a candidate can open a later
    step's URL directly without having finished the ones before it, and
    the sidebar should show that honestly rather than checking off every
    step that merely comes before whichever one you're looking at.
    Defaults to `current_key`'s own position when not given, for the one
    caller (the account-setup step, before any OnboardingPortal.count
    value is meaningful yet) that has no better number to pass.
    """
    idx = step_index(current_key)
    done_count = idx if portal_count is None else portal_count
    steps = [
        {**step, "done": i < done_count}
        for i, step in enumerate(STEPS)
    ]
    total = len(STEPS)
    return {
        "steps": steps,
        "current_step_key": current_key,
        "current_step_number": idx + 1,
        "current_step_label": STEPS[idx]["label"],
        "total_steps": total,
        "progress_pct": round((idx + 1) / total * 100),
        "company": company,
    }
