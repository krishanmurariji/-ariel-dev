"""
gmail_auth/models.py
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from datetime import datetime, timezone as dt_timezone

from base.models import Company
from horilla_views.cbv_methods import render_template
from outlook_auth.methods import sec_to_hm


def _parse_expiry(expiry: str):
    """
    google.oauth2.credentials.Credentials.expiry is a naive UTC datetime,
    so its stored isoformat() string parses back as naive too - attach UTC
    tzinfo explicitly so it can be safely compared/subtracted against
    Django's timezone-aware last_refreshed (USE_TZ=True) without raising
    "can't subtract offset-naive and offset-aware datetimes".
    """
    parsed = datetime.fromisoformat(expiry)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed

# Create your models here.


class GoogleApi(models.Model):
    """
    GoogleApi

    Google OAuth client + token store for a Gmail mailbox, mirroring
    outlook_auth.AzureApi. There is no equivalent of AzureApi's
    outlook_api_endpoint field - googleapiclient.discovery.build("gmail",
    "v1", credentials=...) resolves the Gmail API endpoint internally, so
    there's nothing to configure there.

    Like AzureApi, the client secret and token are stored in plaintext -
    this is a known, pre-existing limitation shared with the Outlook
    integration, not something specific to Gmail.
    """

    google_client_id = models.CharField(max_length=200, verbose_name=_("Client ID"))
    google_client_secret = models.CharField(
        max_length=200, verbose_name=_("Client Secret")
    )
    google_project_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Project ID"),
        help_text=_("Google Cloud project id, for console linkability only."),
    )
    google_email = models.EmailField(verbose_name=_("Email"))
    google_display_name = models.CharField(
        max_length=25, verbose_name=_("Display Name")
    )
    google_redirect_uri = models.URLField(verbose_name=_("Redirect URI"))
    google_authorization_url = models.URLField(
        default="https://accounts.google.com/o/oauth2/v2/auth",
        verbose_name="OAuth authorization endpoint",
    )
    google_token_url = models.URLField(
        default="https://oauth2.googleapis.com/token",
        verbose_name="OAuth token endpoint",
    )
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    is_primary = models.BooleanField(default=False)
    # Stores the fields needed to reconstruct a
    # google.oauth2.credentials.Credentials object: token, refresh_token,
    # token_uri, client_id, client_secret, scopes, expiry (ISO 8601 string).
    token = models.JSONField(editable=False, default=dict)
    oauth_state = models.CharField(editable=False, max_length=100, null=True)
    # google-auth-oauthlib's Flow auto-generates a PKCE code_verifier per
    # authorization_url() call and only keeps it on that in-memory Flow
    # instance - since the callback request builds a brand-new Flow, the
    # verifier has to be persisted here (mirroring oauth_state) or Google
    # rejects the token exchange with "Missing code verifier".
    oauth_code_verifier = models.CharField(
        editable=False, max_length=200, null=True, blank=True
    )
    last_refreshed = models.DateTimeField(null=True, editable=False)

    def save(self, *args, **kwargs):
        if self.is_primary:
            GoogleApi.objects.filter(is_primary=True).update(is_primary=False)
        elif not GoogleApi.objects.filter(is_primary=True).first():
            self.is_primary = True
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.google_display_name} <{self.google_email}>"

    def actions(self):
        return render_template("gmail/actions.html", {"instance": self})

    def token_expire(self):
        """
        last authenticated
        """
        expiry = self.token.get("expiry")
        if self.last_refreshed and expiry:
            duration_seconds = (timezone.now() - self.last_refreshed).seconds
            display = sec_to_hm(duration_seconds)
            expires_at = _parse_expiry(expiry)
            expires_in_seconds = (expires_at - self.last_refreshed).total_seconds()
            if duration_seconds > expires_in_seconds:
                return _("Expired⚠️")
            return f"{display}/{sec_to_hm(expires_in_seconds)}"

    def is_token_expired(self):
        """
        is token expired

        Compares against the absolute `expiry` timestamp Google's
        Credentials object gives us, rather than a relative
        last_refreshed + expires_in pair like AzureApi (Google doesn't
        hand back an "expires_in" seconds value the same way).
        """
        expiry = self.token.get("expiry")
        if not expiry:
            return None
        expires_at = _parse_expiry(expiry)
        return timezone.now() > expires_at
