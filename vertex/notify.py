"""
vertex/notify.py

In-app notifications (the bell) and e-mail for Vertex events. Both are best-effort:
a notification or mail problem never breaks the action that caused it.
"""

import logging

from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

from notifications.signals import notify

logger = logging.getLogger(__name__)


def notify_employees(sender, recipients, verb, redirect="", icon="people-outline"):
    """Bell notification to each employee (``sender`` is an Employee)."""
    sent = 0
    for employee in {e.pk: e for e in recipients if e is not None}.values():
        user = getattr(employee, "employee_user_id", None)
        if user is None or employee == sender:
            continue
        try:
            notify.send(
                sender, recipient=user, verb=verb, icon=icon, redirect=redirect or "/"
            )
            sent += 1
        except Exception:
            logger.exception("Vertex notification failed for %s", employee)
    return sent


def send_plain_email(emails, subject, body):
    """Mail ``body`` (shown pre-formatted) to each address; returns how many went out."""
    from base.backends import ConfiguredEmailBackend

    backend = ConfiguredEmailBackend()
    sender = backend.dynamic_from_email_with_display_name
    html = f'<pre style="font-family:inherit;white-space:pre-wrap">{escape(body)}</pre>'
    sent = 0
    for email in emails:
        try:
            message = EmailMultiAlternatives(subject, body, sender, [email])
            message.attach_alternative(html, "text/html")
            message.send()
            sent += 1
        except Exception:
            logger.exception("Vertex mail to %s failed", email)
    return sent
