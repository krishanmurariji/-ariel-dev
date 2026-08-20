"""
gmail_auth/views.py
"""

import os
from datetime import datetime

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils import timezone
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from horilla.decorators import login_required, permission_required
from gmail_auth import models

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _client_config(api: models.GoogleApi) -> dict:
    """
    Builds the client-config dict google-auth-oauthlib's Flow expects,
    sourced from the GoogleApi DB row - same "credentials live in a DB
    row via the settings UI" pattern as outlook_auth, not env vars.
    """
    return {
        "web": {
            "client_id": api.google_client_id,
            "client_secret": api.google_client_secret,
            "project_id": api.google_project_id,
            "auth_uri": api.google_authorization_url,
            "token_uri": api.google_token_url,
            "redirect_uris": [api.google_redirect_uri],
        }
    }


def _get_api_for_request(request):
    selected_company = request.session.get("selected_company")
    if not selected_company or selected_company == "all":
        return models.GoogleApi.objects.filter(is_primary=True).first()
    return models.GoogleApi.objects.filter(company=selected_company).first()


@login_required
@permission_required("gmail_auth.add_googleapi")
def gmail_login(request):
    """
    gmail login
    """
    api = _get_api_for_request(request)
    if not api:
        messages.info(request, "Not configured Gmail")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    flow = Flow.from_client_config(
        _client_config(api),
        scopes=GMAIL_SCOPES,
        redirect_uri=api.google_redirect_uri,
    )
    # access_type="offline" is Google's equivalent of Outlook's
    # offline_access scope - without it no refresh_token is issued.
    # prompt="consent" forces Google to reissue a refresh_token even if
    # this user already granted access before (Google otherwise omits it
    # on repeat authorizations).
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    api.oauth_state = state
    api.oauth_code_verifier = flow.code_verifier
    api.save()
    return redirect(authorization_url)


def _credentials_to_token_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }


@login_required
@permission_required("gmail_auth.change_googleapi")
def gmail_callback(request):
    """
    gmail callback
    """
    api = _get_api_for_request(request)
    if not api:
        messages.error(request, "Not configured Gmail")
        return redirect("/")

    flow = Flow.from_client_config(
        _client_config(api),
        scopes=GMAIL_SCOPES,
        state=api.oauth_state,
        redirect_uri=api.google_redirect_uri,
        code_verifier=api.oauth_code_verifier,
    )

    authorization_response_uri = request.build_absolute_uri()
    # Google requires https in production, but explicitly allows plain
    # http for localhost redirect URIs during local testing - only
    # force-upgrade the scheme when it's not a localhost callback, so
    # local dev (manage.py runserver, no TLS) keeps working.
    is_localhost = request.get_host().split(":")[0] in ("localhost", "127.0.0.1")
    if not is_localhost:
        authorization_response_uri = authorization_response_uri.replace(
            "http://", "https://"
        )
    else:
        # oauthlib independently refuses to parse an http:// authorization
        # response unless explicitly told it's OK - this is the standard,
        # documented escape hatch for local OAuth testing without TLS.
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    flow.fetch_token(authorization_response=authorization_response_uri)

    api.token = _credentials_to_token_dict(flow.credentials)
    api.last_refreshed = timezone.now()
    api.save()

    return redirect("/")


def refresh_google_token(api: models.GoogleApi) -> models.GoogleApi:
    """
    Refresh a GoogleApi's OAuth token.
    """
    creds = Credentials.from_authorized_user_info(api.token, scopes=GMAIL_SCOPES)
    creds.refresh(Request())
    api.token = _credentials_to_token_dict(creds)
    api.last_refreshed = timezone.now()
    api.save()
    return api


@login_required
@permission_required("gmail_auth.change_googleapi")
def refresh_token(request, pk, *args, **kwargs):
    """
    gmail token refresh
    """
    api = models.GoogleApi.objects.get(pk=pk)
    old_token = api.token.get("token")
    api = refresh_google_token(api)
    if api.token.get("token") == old_token:
        messages.info(request, "Token not refreshed, Login required")
    else:
        messages.success(request, "Token refreshed successfully")

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


def get_valid_credentials(api: models.GoogleApi) -> Credentials:
    """
    Build Credentials for the given GoogleApi, refreshing the token first
    if it has already expired.
    """
    if api.token and api.is_token_expired():
        api = refresh_google_token(api)
    return Credentials.from_authorized_user_info(api.token, scopes=GMAIL_SCOPES)


def _gmail_service(api: models.GoogleApi):
    return build("gmail", "v1", credentials=get_valid_credentials(api))


def list_inbox_messages(
    api: models.GoogleApi,
    since: datetime = None,
    page_token: str = None,
    max_results: int = 100,
):
    """
    List inbox message stubs ({id, threadId}) received after `since`, from
    the mailbox connected via `api`. Unlike Graph's list call, this does
    NOT return full message content - callers must call get_message() per
    id. Returns (messages, next_page_token).
    """
    service = _gmail_service(api)
    query = "in:inbox"
    if since:
        query += f" after:{int(since.timestamp())}"
    response = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results, pageToken=page_token)
        .execute()
    )
    return response.get("messages", []), response.get("nextPageToken")


def get_message(api: models.GoogleApi, message_id: str):
    """
    Fetch a single message's full MIME-tree payload by its Gmail message
    id, for re-processing a specific already-seen email.
    """
    service = _gmail_service(api)
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def get_attachment_bytes_raw(api: models.GoogleApi, message_id: str, attachment_id: str):
    """
    Fetch one attachment's metadata+data ({size, data}) for a message.
    `data` is URL-safe base64 without padding - decode with
    base64.urlsafe_b64decode(), NOT base64.b64decode() (Gmail's encoding
    differs from Microsoft Graph's here - an easy copy-paste trap).
    """
    service = _gmail_service(api)
    return (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )


@login_required
@permission_required("gmail_auth.view_googleapi")
def view_gmail_records(request):
    """
    View server records
    """
    return render(request, "gmail/view_records.html")
