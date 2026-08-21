import ipaddress
import json
import secrets
from urllib.parse import quote, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from horilla.config import logger
from horilla.decorators import login_required, permission_required
from horilla.http.response import HorillaRedirect
from recruitment.ai_linkedin_post import (
    LinkedInPostGenerationError,
    generate_linkedin_post_text,
)
from recruitment.models import LinkedInAccount

LINKEDIN_AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_SCOPES = "openid profile email w_member_social"


def _linkedin_redirect_uri(request):
    return request.build_absolute_uri(reverse("linkedin-callback"))


@login_required
@permission_required("recruitment.change_linkedinaccount")
def linkedin_login(request, pk):
    """
    Starts the LinkedIn OAuth flow for one LinkedInAccount record — redirects
    the browser to LinkedIn's consent screen. Replaces the previous manual
    curl-based token exchange with the same "Connect" pattern gmail_auth uses.
    """
    linkedin_account = LinkedInAccount.find(pk)
    if not linkedin_account:
        return HorillaRedirect(
            request, message=_("No LinkedIn Account found matching the query.")
        )
    if not linkedin_account.client_id or not linkedin_account.client_secret:
        messages.error(
            request,
            _("Add the Client ID and Client Secret first, then connect."),
        )
        return HorillaRedirect(request)

    state = f"{pk}:{secrets.token_urlsafe(24)}"
    linkedin_account.oauth_state = state
    linkedin_account.save()

    params = {
        "response_type": "code",
        "client_id": linkedin_account.client_id,
        "redirect_uri": _linkedin_redirect_uri(request),
        "scope": LINKEDIN_SCOPES,
        "state": state,
    }
    return redirect(f"{LINKEDIN_AUTHORIZATION_URL}?{urlencode(params)}")


@login_required
def linkedin_callback(request):
    """
    Receives LinkedIn's OAuth redirect, exchanges the authorization code for
    an access token, verifies it, and stores it on the LinkedInAccount that
    started the flow (matched via the one-time `state` value).
    """
    setting_list_url = reverse("linkedin-setting-list")

    if request.GET.get("error"):
        messages.error(request, _("LinkedIn authorization was cancelled or denied."))
        return redirect(setting_list_url)

    code = request.GET.get("code")
    state = request.GET.get("state")
    linkedin_account = (
        LinkedInAccount.objects.filter(oauth_state=state).first() if state else None
    )
    if not code or not linkedin_account:
        messages.error(
            request,
            _("LinkedIn authorization failed — the request was invalid or expired."),
        )
        return redirect(setting_list_url)

    token_response = requests.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _linkedin_redirect_uri(request),
            "client_id": linkedin_account.client_id,
            "client_secret": linkedin_account.client_secret,
        },
    )
    if token_response.status_code != 200:
        logger.error(
            "LinkedIn token exchange failed: %s %s",
            token_response.status_code,
            token_response.text,
        )
        messages.error(
            request, _("Could not connect to LinkedIn — check the Client ID/Secret.")
        )
        return redirect(setting_list_url)

    access_token = token_response.json().get("access_token")
    userinfo_response = requests.get(
        LINKEDIN_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    if userinfo_response.status_code != 200:
        messages.error(
            request, _("Connected to LinkedIn but could not verify the account.")
        )
        return redirect(setting_list_url)

    userinfo = userinfo_response.json()
    if userinfo.get("email") != linkedin_account.email:
        messages.error(
            request,
            _(
                "The LinkedIn account you authorized (%(got)s) doesn't match "
                "the Email on this record (%(expected)s)."
            )
            % {"got": userinfo.get("email"), "expected": linkedin_account.email},
        )
        return redirect(setting_list_url)

    linkedin_account.api_token = access_token
    linkedin_account.sub_id = userinfo.get("sub")
    linkedin_account.oauth_state = None
    linkedin_account.save()
    messages.success(request, _("LinkedIn account connected successfully."))
    return redirect(setting_list_url)


@login_required
@permission_required("recruitment.update_linkedinaccount")
def update_isactive_linkedin(request, obj_id):
    """
    htmx function to update is active field in LinkedInAccount.
    Args:
    - is_active: Boolean value representing the state of LinkedInAccount,
    - obj_id: Id of LinkedInAccount object.
    """
    linkedin_account = LinkedInAccount.find(obj_id)
    if not linkedin_account:
        return HorillaRedirect(
            request, message=_("No LinkedIn Account found matching the query.")
        )

    is_active = request.POST.get("is_active")
    if is_active == "on":
        linkedin_account.is_active = True
        messages.success(request, _("LinkedIn Account activated successfully."))
    else:
        linkedin_account.is_active = False
        messages.success(request, _("LinkedIn Account deactivated successfully."))
    linkedin_account.save()

    return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")


@login_required
@permission_required("recruitment.delete_linkedinaccount")
def delete_linkedin_account(request, pk, return_redirect=True):
    """
    Delete Linkedin account
    """
    try:
        if return_redirect:
            LinkedInAccount.objects.get(id=pk).delete()
            messages.success(request, "Linkedin data deleted")
            return redirect(reverse("linkedin-setting-list"))
    except Exception as e:
        logger.error(e)
        messages.error(request, "Something went wrong")
    return HorillaRedirect(request)


@login_required
def validate_linkedin_token(request, pk):
    linkedin_account = LinkedInAccount.find(pk)
    if not linkedin_account:
        return HorillaRedirect(
            request, message=_("No LinkedIn Account found matching the query.")
        )

    access_token = linkedin_account.api_token
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        messages.success(request, _("LinkedIn connection success."))
    else:
        messages.success(request, _("LinkedIn connection failed."))
    return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")


def html_to_text(html):
    """
    Converts rich-text job description HTML to plain text for LinkedIn.
    Handles bullet lists (Roles & Responsibilities, Requirements, Skills,
    etc.) and headers, not just <p>/<br> — job descriptions from a WYSIWYG
    editor are almost always <ul><li> lists, which get silently dropped if
    only <p>/<br> are considered.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "• ")
    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
        tag.append("\n")
    lines = [line.strip() for line in soup.get_text().splitlines()]
    return "\n".join(line for line in lines if line)


LINKEDIN_API_VERSION = "202608"  # YYYYMM — LinkedIn's REST Posts API requires this,
# and rejects stale values with 426 Upgrade Required. Bump periodically.


def _linkedin_rest_headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
    }


def _fallback_commentary(recruitment, description_text, skill_titles):
    """
    Plain, deterministic post text used only if the AI draft
    (generate_linkedin_post_text) fails - keeps "Publish to LinkedIn"
    working even when the AI provider is unreachable or misconfigured.
    Deliberately never includes the raw apply URL or an email address, for
    the same reason the AI prompt is told not to: the link card
    (content.article, built in post_recruitment_in_linkedin) is what
    carries the actual clickable "Apply" link.
    """
    header = f"🚀 We're Hiring: {recruitment.title}\n🏢 {recruitment.company_id}\n"
    skills_block = f"\n\n🛠️ Skills: {', '.join(skill_titles)}" if skill_titles else ""
    footer = (
        "\n\n👉 Apply using the link attached to this post."
        "\n\n#Hiring #JobOpening #NowHiring #Careers"
    )

    # LinkedIn caps post text around 3000 characters — trim only the
    # description (never the header/skills/apply footer) to fit.
    fixed_parts_len = len(header) + len(skills_block) + len(footer) + 2  # +2 = "\n\n"
    max_description_len = max(0, 3000 - fixed_parts_len)
    if len(description_text) > max_description_len:
        description_text = description_text[: max_description_len - 1].rstrip() + "…"

    return f"{header}\n{description_text}{skills_block}{footer}"


def _looks_publicly_reachable(site_url):
    """
    False for localhost/loopback/private-network addresses - i.e. exactly
    what a staff member's own browser session is on almost every time they
    save a recruitment from a local/internal environment. Not a real
    reachability check (nothing here actually contacts the host), so a
    genuinely broken but public-looking domain can still slip through -
    this only exists to catch the common case of accidentally publishing a
    job whose Apply link points at a machine only the poster can reach,
    which is what was showing candidates a "Page not found" instead of the
    application form.
    """
    host = urlparse(site_url).hostname or ""
    if not host or host in ("localhost", "0.0.0.0") or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a real domain name, not a bare IP - good enough
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def post_recruitment_in_linkedin(
    request, recruitment, linkedin_acc, feed_type="feed", group_id=None
):
    """
    Publishes a recruitment as a LinkedIn post via the current REST Posts API
    (https://api.linkedin.com/rest/posts). The older /v2/ugcPosts endpoint this
    used previously is LinkedIn's deprecated "Legacy" API and rejects requests
    from newer apps with a 400.

    Note: group_id/feed_type="group" isn't exercised anywhere in this codebase
    and group distribution isn't verified under the new API — only the default
    main-feed post (feed_type="feed") is implemented here.

    Returns True on success, False otherwise, so callers (e.g.
    RecruitmentForm.form_valid) can decide whether it's safe to clean up an
    old post that this one is meant to replace.
    """
    # Same host resolution the "Generate Dynamic Link" feature already uses
    # (RecruitmentDynamicLinkView, recruitment/cbv/recruitment_view.py) -
    # whatever domain the current request actually came in on. That's
    # correct when this runs from the real deployed site; from a local/
    # internal session it's localhost/an internal IP instead, which isn't a
    # reason to block posting outright (that would make the LinkedIn
    # integration untestable from local dev) - just warn, since the Apply
    # link in that post genuinely won't work for anyone outside this
    # machine until the same job gets re-published from the real site.
    site_url = f"{request.scheme}://{request.get_host()}"
    if not _looks_publicly_reachable(site_url):
        logger.warning(
            "Publishing recruitment %s to LinkedIn from a non-public "
            "address (%s) - the Apply link in this post will only work "
            "from this machine.",
            recruitment.id,
            site_url,
        )
        messages.warning(
            request,
            _(
                "Published to LinkedIn, but heads up: since this was done "
                "from a local/test environment, the Apply link in that "
                "post only works from this computer. Publish this job "
                "again from the real site before candidates rely on it."
            ),
        )

    # utm_source has to be this exact name, not just "source" - it's what
    # application_form() (recruitment/views/surveys.py) and the hidden
    # utm_source field on the application form template actually read to
    # tag the resulting Candidate's Platform. Anything else is silently
    # ignored and applicants from this post won't show up as "linkedin".
    recruitment_url = (
        f"{site_url}/recruitment/application-form"
        f"?recruitmentId={recruitment.id}&utm_source=linkedin"
    )
    description_text = html_to_text(recruitment.description)
    skill_titles = list(recruitment.skills.values_list("title", flat=True))

    try:
        commentary = generate_linkedin_post_text(
            recruitment, description_text, skill_titles
        )
    except LinkedInPostGenerationError as exc:
        logger.info(
            "LinkedIn AI post generation failed for recruitment %s (%s); "
            "using the fallback template instead.",
            recruitment.id,
            exc,
        )
        commentary = _fallback_commentary(recruitment, description_text, skill_titles)

    if len(commentary) > 3000:
        commentary = commentary[:2999].rstrip() + "…"

    payload_dict = {
        "author": f"urn:li:person:{linkedin_acc.sub_id}",
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "article": {
                "source": recruitment_url,
                "title": recruitment.title,
                "description": description_text[:200]
                or f"Apply now for {recruitment.title}",
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    url = "https://api.linkedin.com/rest/posts"
    payload = json.dumps(payload_dict)
    headers = _linkedin_rest_headers(linkedin_acc.api_token)
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=30)
    except requests.RequestException as exc:
        logger.error(
            "LinkedIn post request failed for recruitment %s: %s", recruitment.id, exc
        )
        messages.error(
            request,
            _("Couldn't reach LinkedIn just now. Please try again in a moment."),
        )
        return False

    if response.status_code == 201:
        # The new API returns the post's URN in this response header, not the body.
        recruitment.linkedin_post_id = response.headers.get("x-restli-id")
        recruitment.save()
        return True

    logger.error(
        "LinkedIn post failed for recruitment %s: %s %s",
        recruitment.id,
        response.status_code,
        response.text,
    )

    if response.status_code == 429:
        # Rate limiting is temporary — LinkedIn caps how many posts an app
        # can publish in a stretch of time. Leaving publish_in_linkedin
        # untouched (unlike the branch below) means the very next save that
        # actually changes the job's content will simply try again, instead
        # of the recruiter having to notice the feature got silently turned
        # off and switch it back on themselves.
        messages.error(
            request,
            _(
                "LinkedIn is asking us to slow down — too many posts were "
                "published in a short time. This job will try posting again "
                "automatically the next time it's saved; please wait a few "
                "minutes before saving it again."
            ),
        )
        return False

    messages.error(
        request,
        _(
            "This job couldn't be published to LinkedIn, so LinkedIn "
            "posting has been turned off for it. Check the LinkedIn "
            "account connection in Settings, then turn it back on."
        ),
    )
    recruitment.publish_in_linkedin = False
    recruitment.save()
    return False


def delete_post(recruitment, post_id=None):
    """
    Deletes a LinkedIn post — by default the recruitment's current
    linkedin_post_id, or an explicit post_id when deleting one that's no
    longer current (see RecruitmentForm.form_valid, which publishes a
    replacement post first and only deletes the old id once that succeeds,
    so a failed re-publish never leaves the job with no live LinkedIn post
    at all).
    """
    target_post_id = post_id or recruitment.linkedin_post_id
    if not target_post_id:
        return True  # 787

    url = f"https://api.linkedin.com/rest/posts/{quote(target_post_id, safe='')}"
    headers = _linkedin_rest_headers(recruitment.linkedin_account_id.api_token)

    try:
        response = requests.delete(url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        logger.error(
            "LinkedIn delete request failed for recruitment %s: %s",
            recruitment.id,
            exc,
        )
        return False

    if response.status_code == 204:
        if target_post_id == recruitment.linkedin_post_id:
            recruitment.linkedin_post_id = None
            recruitment.save()
        return True

    logger.error(
        "LinkedIn delete failed for recruitment %s (post %s): %s %s",
        recruitment.id,
        target_post_id,
        response.status_code,
        response.text,
    )
    return False
