"""
outlook_auth/views.py
"""

from datetime import datetime

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from requests_oauthlib import OAuth2Session

from horilla.decorators import login_required, permission_required
from outlook_auth import models


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


def refresh_outlook_token(api: models.AzureApi):
    """
    Refresh Outlook token
    """
    oauth = OAuth2Session(
        api.outlook_client_id,
        token=api.token,
        auto_refresh_kwargs={
            "client_id": api.outlook_client_id,
            "client_secret": api.outlook_client_secret,
        },
        auto_refresh_url=api.outlook_token_url,
    )
    new_token = oauth.refresh_token(
        api.outlook_token_url,
        refresh_token=api.token["refresh_token"],
        client_id=api.outlook_client_id,
        client_secret=api.outlook_client_secret,
    )
    api.token = new_token
    api.last_refreshed = datetime.now()
    api.save()
    return api


@login_required
@permission_required("outlook_auth.change_azureapi")
def refresh_token(request, pk, *args, **kwargs):
    """
    outlook_freshe
    """
    api = models.AzureApi.objects.get(pk=pk)
    old_token = api.token.get("access_token")
    api = refresh_outlook_token(api)
    if api.token.get("access_token") == old_token:
        messages.info(request, _("Token not refreshed, Login required"))
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
    api.last_refreshed = datetime.now()
    token = oauth.fetch_token(
        api.outlook_token_url,
        client_secret=api.outlook_client_secret,
        authorization_response=authorization_response_uri,  # Use the modified URI
    )
    api.token = token
    api.save()

    return redirect("/")


def send_outlook_email(request, email_data=None):
    """
    send mail
    """
    selected_company = None
    if request:
        selected_company = request.session.get("selected_company")
    if not selected_company or selected_company == "all":
        api = models.AzureApi.objects.filter(is_primary=True).first()
    else:
        api = models.AzureApi.objects.filter(company=selected_company).first()
    token = api.token
    if not token and not api.is_token_expired():
        refresh_outlook_token(api)
    if not token and request:
        messages.info(request, "Mail not sent")
        return redirect("outlook_login")

    oauth = OAuth2Session(
        api.outlook_client_id,
        token=token,
        auto_refresh_kwargs={
            "client_id": api.outlook_client_id,
            "client_secret": api.outlook_client_secret,
        },
        auto_refresh_url=api.outlook_token_url,
    )
    response = oauth.post(f"{api.outlook_api_endpoint}/me/sendMail", json=email_data)

    try:
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        messages.success(request, _("Mail sent"))
        # Email sent successfully!
        return response, email_data
    except Exception as e:
        messages.error(_("Something went wrong"))
        messages.info(_("Outlook authentication required/expired"))
        return None, email_data


def get_valid_oauth_session(api: models.AzureApi):
    """
    Build an OAuth2Session for the given AzureApi, refreshing the token
    first if it has already expired.
    """
    if api.token and api.is_token_expired():
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
