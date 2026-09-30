"""
accessibility/middlewares.py
"""

from django.core.cache import cache

from accessibility.methods import check_is_accessible
from accessibility.models import ACCESSBILITY_FEATURE


def accessibility_cache_key(employee_id):
    """
    check_is_accessible()'s result only ever depends on the employee (and
    the DefaultAccessibility config, invalidated separately in signals.py) -
    never on which session/browser/device they're using. One key per
    employee, not one per session, means a user logged in from several
    places shares a single cache entry (correct, since the answer is the
    same for all of them) and there is nothing left to invalidate but that
    one key - no side index of "which cache keys belong to this user" to
    keep in sync or leak.
    """
    return f"accessibility_features_{employee_id}"


def update_accessibility_cache(cache_key, employee):
    """Cache for get all the queryset"""
    feature_accessible = {}
    for accessibility, _display in ACCESSBILITY_FEATURE:
        feature_accessible[accessibility] = check_is_accessible(
            accessibility, cache_key, employee
        )
    cache.set(cache_key, feature_accessible)


class AccessibilityMiddleware:
    """
    AccessibilityMiddleware
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        employee = getattr(request.user, "employee_get", None)
        if employee and cache.get(accessibility_cache_key(employee.id)) is None:
            update_accessibility_cache(accessibility_cache_key(employee.id), employee)
        response = self.get_response(request)
        return response
