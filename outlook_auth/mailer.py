"""
outlook_auth/mailer.py

Sends Django EmailMessage/EmailMultiAlternatives objects through the
Microsoft Graph API (/me/sendMail) using a company's connected Outlook
account, as an alternative to SMTP. Used by
base.backends.ConfiguredEmailBackend when a company's
DynamicEmailConfiguration.send_via is set to "outlook".
"""

import base64
from email.mime.base import MIMEBase
from email.utils import parseaddr

from outlook_auth.models import AzureApi
from outlook_auth.views import get_valid_oauth_session


def get_azure_api_for_company(company):
    """
    Resolve the AzureApi row to use for a given company, falling back to
    whichever account is marked primary - mirrors the lookup used by
    DefaultHorillaMailBackend.get_dynamic_email_config().
    """
    api = None
    if company:
        api = AzureApi.objects.filter(company=company).first()
    if api is None:
        api = AzureApi.objects.filter(is_primary=True).first()
    return api


def _graph_recipients(addresses):
    """
    Django recipient lists can hold either bare emails or
    "Display Name <email@x.com>" formatted strings. Graph's `address`
    field needs the bare email - a formatted string, or a name with no
    actual address, gets the whole message rejected by Graph (e.g.
    "ErrorParticipantDoesntHaveAnEmailAddress"), so parse and drop
    anything that doesn't resolve to a real address.
    """
    recipients = []
    for raw in addresses or []:
        if not raw:
            continue
        name, address = parseaddr(raw)
        if not address:
            continue
        entry = {"address": address}
        if name:
            entry["name"] = name
        recipients.append({"emailAddress": entry})
    return recipients


def _graph_attachments(email_message):
    attachments = []
    for attachment in getattr(email_message, "attachments", []):
        if isinstance(attachment, MIMEBase):
            content_id = (attachment.get("Content-ID") or "").strip("<>")
            content_bytes = attachment.get_payload(decode=True)
            if not content_bytes:
                continue
            entry = {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": attachment.get_filename() or content_id or "attachment",
                "contentType": attachment.get_content_type(),
                "contentBytes": base64.b64encode(content_bytes).decode("ascii"),
            }
            if content_id:
                entry["contentId"] = content_id
                entry["isInline"] = "inline" in (attachment.get("Content-Disposition") or "")
            attachments.append(entry)
        else:
            filename, content, mimetype = (list(attachment) + [None, None])[:3]
            if content is None:
                continue
            if isinstance(content, str):
                content = content.encode("utf-8")
            attachments.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename or "attachment",
                    "contentType": mimetype or "application/octet-stream",
                    "contentBytes": base64.b64encode(content).decode("ascii"),
                }
            )
    return attachments


def _graph_message_payload(email_message):
    alt_html = None
    for content, mimetype in getattr(email_message, "alternatives", None) or []:
        if mimetype == "text/html":
            alt_html = content
    is_html = alt_html or getattr(email_message, "content_subtype", "plain") == "html"
    body_content = alt_html or email_message.body

    payload = {
        "subject": email_message.subject,
        "body": {"contentType": "HTML" if is_html else "Text", "content": body_content},
        "toRecipients": _graph_recipients(email_message.to),
        "ccRecipients": _graph_recipients(email_message.cc),
        "bccRecipients": _graph_recipients(email_message.bcc),
        "attachments": _graph_attachments(email_message),
    }
    reply_to = _graph_recipients(email_message.reply_to)
    if reply_to:
        payload["replyTo"] = reply_to
    return {"message": payload, "saveToSentItems": "true"}


def send_via_outlook(email_messages, company=None, fail_silently=False):
    """
    Send a list of Django EmailMessage objects through Microsoft Graph's
    /me/sendMail using the Outlook account connected for `company` (or
    whichever account is marked primary). Returns the number of messages
    sent successfully, matching Django email backend's send_messages()
    contract. Raises RuntimeError with the underlying Graph error detail
    on failure unless fail_silently is True.
    """
    api = get_azure_api_for_company(company)
    if api is None or not api.token:
        if fail_silently:
            return 0
        raise RuntimeError(
            "No connected Outlook account is available to send mail - "
            "check Settings > Mail Servers > Outlook."
        )

    oauth = get_valid_oauth_session(api)
    sent_count = 0
    for email_message in email_messages:
        payload = _graph_message_payload(email_message)
        response = oauth.post(f"{api.outlook_api_endpoint}/me/sendMail", json=payload)
        if response.ok:
            sent_count += 1
        elif not fail_silently:
            raise RuntimeError(
                f"Outlook (Graph API) rejected the mail: "
                f"{response.status_code} {response.text[:500]}"
            )
    return sent_count
