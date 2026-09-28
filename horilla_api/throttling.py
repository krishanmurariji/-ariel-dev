"""
horilla_api/throttling.py

DRF's stock throttles identify an anonymous client by the *whole*
X-Forwarded-For header when it is present (NUM_PROXIES unset). That header
is client-supplied, so sending a different fake value on every request gave
every request its own fresh throttle bucket - the login throttle could be
dodged entirely. These subclasses identify the client the same way
django-axes is configured to (see AXES_IPWARE_META_PRECEDENCE_ORDER in
horilla/settings/base.py): base.methods.get_client_ip, which prefers the
X-Real-IP header the reverse proxy sets itself and falls back to
REMOTE_ADDR.
"""

from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)

from base.methods import get_client_ip


class TrustedClientIPMixin:
    def get_ident(self, request):
        return get_client_ip(request)


class TrustedAnonRateThrottle(TrustedClientIPMixin, AnonRateThrottle):
    pass


class TrustedUserRateThrottle(TrustedClientIPMixin, UserRateThrottle):
    pass


class TrustedScopedRateThrottle(TrustedClientIPMixin, ScopedRateThrottle):
    pass
