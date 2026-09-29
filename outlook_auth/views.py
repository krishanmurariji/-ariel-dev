"""
outlook_auth/views.py
"""

import logging
import time
from datetime import datetime, timedelta

import requests
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from requests_oauthlib import OAuth2Session

from horilla.decorators import login_required, permission_required
from outlook_auth import models

logger = logging.getLogger(__name__)

# How long to leave a "sign in again" account alone before trying to renew it
# once more (so it recovers by itself if the Microsoft-side policy is fixed).
REAUTH_RETRY_AFTER = timedelta(minutes=30)

# Microsoft answers that mean "the stored login can no longer be renewed
# silently; the user has to sign in again": MFA required (50076/50079/50158),
# refresh token expired or revoked (70008/700082/50173/50132), consent or
# conditional-access blocks (65001/53003).
_REAUTH_MARKERS = (
    "interaction_required",
    "invalid_grant",
    "consent_required",
    "AADSTS50076",
    "AADSTS50079",
    "AADSTS50158",
    "AADSTS70008",
    "AADSTS700082",
    "AADSTS50173",
    "AADSTS50132",
    "AADSTS65001",
    "AADSTS53003",
)


class OutlookReauthRequired(Exception):
    """The Outlook connection has to be signed in again by a person."""


def _notify(request, level, text):
    """Show a message only when there is a real request to attach it to."""
    if request is not None and hasattr(request, "_messages"):
        level(request, text)


@login_required
@permission_required("outlook_auth.add_azureapi")
def outlook_login(request):
    """
    outlook login
    """
    selected_company = request.session.get("selected_company")
    if not selected_company or selected_company == "all":
        api = models.AzureApi.objects.filter(is_primary=True).first()
    else:
        api = models.AzureApi.objects.filter(company=selected_company).first()

    if not api:
        messages.info(request, "Not configured outlook")
        return redirect("/")
    oauth = OAuth2Session(
        api.outlook_client_id,
        redirect_uri=api.outlook_redirect_uri,
        # Calendars.ReadWrite added for Teams meeting creation (planned
        # recruitment interview integration, mirrors horilla_meet's Google
        # Meet flow). Existing connected accounts need to re-consent once
        # to pick up this scope - see outlook_login's docs/settings page.
        scope=["Mail.Send", "Mail.Read", "Calendars.ReadWrite", "offline_access"],
    )
    authorization_url, state = oauth.authorization_url(api.outlook_authorization_url)

    api.oauth_state = state
    api.save()
    cache.set("oauth_state", state)
    return redirect(authorization_url)


def _record_refresh_failure(api, message, reauth):
    api.reauth_required = reauth
    api.last_error = message[:500]
    api.last_error_at = timezone.now()
    api.save(update_fields=["reauth_required", "last_error", "last_error_at"])


def refresh_outlook_token(api: models.AzureApi):
    """
    Renew the access token from the stored refresh token.

    Talks to Microsoft directly (instead of through requests_oauthlib) so the
    real answer - e.g. "AADSTS50076: multi-factor authentication required" -
    is kept rather than replaced by a generic "missing access token" error.
    Raises OutlookReauthRequired when only a person signing in again can fix
    it (and remembers that on the record); any other failure raises a plain
    error and leaves the record as it was.
    """
    refresh_token = (api.token or {}).get("refresh_token")
    if not refresh_token:
        message = "No refresh token is stored; sign in to Outlook again."
        _record_refresh_failure(api, message, reauth=True)
        raise OutlookReauthRequired(message)

    response = requests.post(
        api.outlook_token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": api.outlook_client_id,
            "client_secret": api.outlook_client_secret,
        },
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code != 200 or "access_token" not in payload:
        code = payload.get("error") or f"HTTP {response.status_code}"
        # Microsoft appends trace ids on later lines; the first line is the reason.
        detail = (payload.get("error_description") or "").splitlines()
        message = f"{code}: {detail[0] if detail else ''}".strip()[:500]
        needs_signin = any(
            marker in f"{code} {' '.join(detail)}" for marker in _REAUTH_MARKERS
        )
        _record_refresh_failure(api, message, reauth=needs_signin)
        if needs_signin:
            raise OutlookReauthRequired(
                "Outlook has to be connected again (Microsoft: " + message + ")"
            )
        raise RuntimeError("Outlook token refresh failed: " + message)

    token = dict(payload)
    token.setdefault("refresh_token", refresh_token)
    token["expires_at"] = time.time() + int(token.get("expires_in") or 0)
    api.token = token
    api.last_refreshed = timezone.now()
    api.reauth_required = False
    api.last_error = ""
    api.last_error_at = None
    api.save()
    return api


def refresh_is_due(api: models.AzureApi):
    """False while an account waiting for a sign-in was tried very recently."""
    if not api.reauth_required or not api.last_error_at:
        return True
    return timezone.now() - api.last_error_at >= REAUTH_RETRY_AFTER


@login_required
@permission_required("outlook_auth.change_azureapi")
def refresh_token(request, pk, *args, **kwargs):
    """
    outlook_freshe
    """
    api = models.AzureApi.objects.get(pk=pk)
    try:
        refresh_outlook_token(api)
    except OutlookReauthRequired as error:
        messages.error(
            request,
            _("Outlook needs to be connected again - use the Login button. ")
            + str(error),
        )
    except Exception as error:  # noqa: BLE001 - shown to the admin, logged below
        logger.exception("Outlook token refresh failed")
        messages.error(request, _("Token not refreshed: ") + str(error))
    else:
        messages.success(request, _("Token refreshed successfully"))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@permission_required("outlook_auth.change_azureapi")
def outlook_callback(request):
    """
    outlook callback
    """
    selected_company = request.session.get("selected_company")
    if not selected_company or selected_company == "all":
        api = models.AzureApi.objects.filter(is_primary=True).first()
    else:
        api = models.AzureApi.objects.filter(company=selected_company).first()

    if not api:
        messages.error(request, _("Outlook is not configured."))
        return redirect("/")

    state = api.oauth_state

    oauth = OAuth2Session(
        api.outlook_client_id,
        state=state,
        redirect_uri=api.outlook_redirect_uri,
    )

    authorization_response_uri = request.build_absolute_uri()
    authorization_response_uri = authorization_response_uri.replace(
        "http://", "https://"
    )
    try:
        token = oauth.fetch_token(
            api.outlook_token_url,
            client_secret=api.outlook_client_secret,
            authorization_response=authorization_response_uri,  # Use the modified URI
        )
    except Exception as error:  # noqa: BLE001 - shown to the admin, logged below
        logger.exception("Outlook sign-in could not be completed")
        messages.error(request, _("Outlook sign-in failed: ") + str(error))
        return redirect("/")
    api.token = token
    api.last_refreshed = timezone.now()
    api.reauth_required = False
    api.last_error = ""
    api.last_error_at = None
    api.save()
    messages.success(request, _("Outlook connected."))

    return redirect("/")


def send_outlook_email(request, email_data=None):
    """
    Send one prepared message through the connected Outlook account.
    Always returns (response, email_data); response is None when it failed.
    ``request`` may be None (mail sent from a background thread).
    """
    from outlook_auth.methods import get_azure_api

    api = get_azure_api(request)
    if api is None or not api.token:
        _notify(request, messages.info, _("Mail not sent: Outlook is not connected."))
        return None, email_data

    try:
        oauth = get_valid_oauth_session(api)
        response = oauth.post(f"{api.outlook_api_endpoint}/me/sendMail", json=email_data)
        response.raise_for_status()  # bad status codes (4xx/5xx) raise
    except OutlookReauthRequired as error:
        logger.warning("Outlook mail not sent: %s", error)
        _notify(request, messages.error, _("Outlook needs to be connected again."))
        return None, email_data
    except Exception:  # noqa: BLE001
        logger.exception("Outlook mail not sent")
        _notify(request, messages.error, _("Something went wrong sending the mail."))
        return None, email_data
    _notify(request, messages.success, _("Mail sent"))
    return response, email_data


def get_valid_oauth_session(api: models.AzureApi):
    """
    Build an OAuth2Session for the given AzureApi, refreshing the token
    first if it has already expired.
    """
    if api.token and api.is_token_expired():
        if not refresh_is_due(api):
            raise OutlookReauthRequired(
                "Outlook has to be connected again (Microsoft: " + api.last_error + ")"
            )
        api = refresh_outlook_token(api)
    return OAuth2Session(
        api.outlook_client_id,
        token=api.token,
        auto_refresh_kwargs={
            "client_id": api.outlook_client_id,
            "client_secret": api.outlook_client_secret,
        },
        auto_refresh_url=api.outlook_token_url,
    )


def list_inbox_messages(
    api: models.AzureApi, since: datetime = None, next_link: str = None
):
    """
    List one page of inbox messages (with attachments expanded inline)
    received after `since`, from the mailbox connected via `api`. Requires
    the Mail.Read scope to have been consented to.

    Returns (messages, next_link) - `next_link` is Graph's `@odata.nextLink`
    (a full URL, already carrying every query param) if more pages exist
    beyond this one, else None. Pass a previous call's `next_link` back in
    to fetch the following page - `since` is ignored in that case since
    the link already encodes the original filter.
    """
    oauth = get_valid_oauth_session(api)
    if next_link:
        response = oauth.get(next_link)
    else:
        params = {
            "$select": "id,subject,from,receivedDateTime,body",
            "$expand": "attachments",
            "$orderby": "receivedDateTime asc",
            "$top": 25,
        }
        if since:
            params["$filter"] = (
                f"receivedDateTime gt {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )
        response = oauth.get(
            f"{api.outlook_api_endpoint}/me/mailFolders/inbox/messages",
            params=params,
        )
    response.raise_for_status()
    payload = response.json()
    return payload.get("value", []), payload.get("@odata.nextLink")


def get_message(api: models.AzureApi, message_id: str):
    """
    Fetch a single message (with attachments expanded inline) by its
    Graph message id, for re-processing a specific already-seen email.
    """
    oauth = get_valid_oauth_session(api)
    response = oauth.get(
        f"{api.outlook_api_endpoint}/me/messages/{message_id}",
        params={"$select": "id,subject,from,receivedDateTime,body", "$expand": "attachments"},
    )
    response.raise_for_status()
    return response.json()


@login_required
@permission_required("outlook_auth.view_azureapi")
def view_outlook_records(request):
    """
    View server records
    """
    return render(request, "outlook/view_records.html")
