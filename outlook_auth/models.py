"""
outlook_auth/models.py
"""

import time

from django.apps import apps
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel, NoPermissionModel
from horilla_views.cbv_methods import render_template
from outlook_auth.crypto_fields import EncryptedJSONField, EncryptedTextField
from outlook_auth.methods import sec_to_hm

# Create your models here.


class AzureApi(models.Model):
    """
    AzureApi
    """

    outlook_client_id = models.CharField(max_length=200, verbose_name=_("Client ID"))
    outlook_client_secret = EncryptedTextField(verbose_name=_("Client Secret"))
    outlook_tenant_id = models.CharField(max_length=200, verbose_name=_("Tenant ID"))
    outlook_email = models.EmailField(verbose_name=_("Email"))
    outlook_display_name = models.CharField(
        max_length=25, verbose_name=_("Display Name")
    )
    outlook_redirect_uri = models.URLField(verbose_name=("Redirect URi"))
    outlook_authorization_url = models.URLField(
        default="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        verbose_name="OAuth authorization endpoint",
    )
    outlook_token_url = models.URLField(
        default="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        verbose_name="OAuth token endpoint",
    )
    outlook_api_endpoint = models.URLField(
        default="https://graph.microsoft.com/v1.0",
        verbose_name="Microsoft Graph API endpoint",
    )
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    is_primary = models.BooleanField(default=False)
    token = EncryptedJSONField(editable=False, default=dict)
    oauth_state = models.CharField(editable=False, max_length=100, null=True)
    last_refreshed = models.DateTimeField(null=True, editable=False)
    # Set when Microsoft refuses to renew the login without the user signing
    # in again (e.g. AADSTS50076: multi-factor authentication required).
    reauth_required = models.BooleanField(default=False, editable=False)
    last_error = models.CharField(
        max_length=500, blank=True, default="", editable=False
    )
    last_error_at = models.DateTimeField(null=True, blank=True, editable=False)

    def save(self, *args, **kwargs):
        if self.is_primary:
            AzureApi.objects.filter(is_primary=True).update(is_primary=False)
        elif not AzureApi.objects.filter(is_primary=True).first():
            self.is_primary = True
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.outlook_display_name} <{self.outlook_email}>"

    def actions(self):
        return render_template("outlook/actions.html", {"instance": self})

    def token_expire(self):
        """
        last authenticated
        """
        if self.reauth_required:
            return _("Reconnect needed⚠️")
        if self.last_refreshed:
            # total_seconds(), not .seconds: .seconds is only the leftover
            # part of a day, so after 24h it wrapped back to a small number
            # and an old, dead token looked fresh.
            duration_seconds = int((timezone.now() - self.last_refreshed).total_seconds())
            expires_in_seconds = int((self.token or {}).get("expires_in") or 0)
            if duration_seconds > expires_in_seconds:
                return _("Expired⚠️")
            return f"{sec_to_hm(duration_seconds)}/{sec_to_hm(expires_in_seconds)}"

    def is_token_expired(self, margin_seconds=120):
        """
        True when the access token has run out (or will within
        ``margin_seconds``). None/False when there is no timing information.
        """
        token = self.token or {}
        expires_at = token.get("expires_at")
        if expires_at:
            return time.time() >= float(expires_at) - margin_seconds
        if self.last_refreshed:
            elapsed = (timezone.now() - self.last_refreshed).total_seconds()
            return elapsed >= int(token.get("expires_in") or 0) - margin_seconds
        return False


class OutlookMeeting(HorillaModel):
    """
    A Teams meeting created via Microsoft Graph (calendar event with
    isOnlineMeeting=True / onlineMeetingProvider="teamsForBusiness").
    Mirrors horilla_meet.GoogleMeeting.
    """

    employee_id = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="outlook_meetings"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    join_url = models.URLField(max_length=2048, blank=True)
    event_id = models.CharField(max_length=255, null=True, blank=True)
    duration = models.PositiveIntegerField(
        default=60, help_text=_("Duration in minutes")
    )
    attendees = models.JSONField(default=list, blank=True, null=True)
    is_online = models.BooleanField(
        default=True,
        help_text=_(
            "Whether this event should include a Teams video link. False "
            "creates a plain calendar event with no meeting link."
        ),
    )

    class Meta:
        ordering = ["-start_time"]

    def __str__(self):
        return f"{self.title} - {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    @property
    def end_time(self):
        from datetime import timedelta

        return self.start_time + timedelta(minutes=self.duration)


if apps.is_installed("recruitment"):
    from recruitment.models import InterviewSchedule

    class InterviewOutlookMeetingLink(models.Model, NoPermissionModel):
        interview = models.OneToOneField(
            InterviewSchedule,
            on_delete=models.CASCADE,
            related_name="outlook_interviews",
        )
        meeting = models.OneToOneField(OutlookMeeting, on_delete=models.CASCADE)
