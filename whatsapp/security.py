"""
whatsapp/security.py

Meta signs every webhook delivery: the ``X-Hub-Signature-256`` header holds
``sha256=`` plus the HMAC-SHA256 of the raw request body, keyed with the Meta
app's *App Secret*. Without checking it, anyone who finds the webhook URL can
post forged "incoming messages" that the app will act on.

Verification switches on as soon as an App Secret is saved on a WhatsApp
credential (WhatsApp credentials > App Secret). Until then there is nothing to
check against, so deliveries are still accepted - but each one is logged so the
gap stays visible.
"""

import hashlib
import hmac

SIGNATURE_HEADER = "HTTP_X_HUB_SIGNATURE_256"
SIGNATURE_PREFIX = "sha256="


def configured_app_secrets():
    from whatsapp.models import WhatsappCredientials

    # Unscoped on purpose: the webhook is called by Meta, not by a logged-in
    # user, so there is no company to scope to.
    return [
        credential.meta_app_secret
        for credential in WhatsappCredientials._base_manager.all()
        if credential.meta_app_secret
    ]


def verify_meta_signature(request):
    """
    True  - the signature matches one of the configured App Secrets
    False - a secret is configured but the signature is missing or wrong
    None  - no App Secret is configured, so nothing can be verified
    """
    secrets = configured_app_secrets()
    if not secrets:
        return None
    header = request.META.get(SIGNATURE_HEADER, "")
    if not header.startswith(SIGNATURE_PREFIX):
        return False
    given = header[len(SIGNATURE_PREFIX) :]
    for secret in secrets:
        expected = hmac.new(
            secret.encode(), request.body, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, given):
            return True
    return False
