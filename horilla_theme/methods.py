"""
horilla_theme/methods.py
"""

from base.models import Company
from horilla_theme.models import CompanyTheme


def get_active_company(request):
    """
    Resolves the Company instance currently active for the logged-in
    user's selected company (the company switcher), same resolution
    get_active_company_theme uses.
    """
    selected_company = request.session.get("selected_company")
    return (
        Company.objects.filter(id=selected_company).first()
        if selected_company and selected_company != "all"
        else None
    )


def get_active_company_theme(request):
    """
    Resolves the HorillaColorTheme currently active for the logged-in
    user's selected company (Settings > Theme Manager) - same resolution
    horilla_theme.views.ThemeView uses, so any UI reading this stays in
    sync with what an admin has configured, instead of a hardcoded color.
    """
    active_company = get_active_company(request)
    return CompanyTheme.get_theme_for_company(active_company)
