"""
base/mail_helpers.py

Small building blocks for the built-in (hardcoded) notification mails, so each new mail is a
few lines of wording and not a copy of the layout code.

* ``send_branded_mail`` - one mail inside the company letterhead; never raises.
* ``run_in_background`` - sends from a thread, so a slow or unreachable mail server cannot
  hold up (or break) the action the person just took. Work out anything that needs the request
  (such as a link) before calling it: the thread has no request.
* ``paragraph`` / ``info_card`` / ``action_button`` - the pieces of the body.
"""

import logging
import threading

from django.utils.html import escape

from base.backends import ConfiguredEmailBackend
from base.mail_letterhead import build_letterhead_email

logger = logging.getLogger(__name__)


def run_in_background(func, *args, **kwargs):
    """Run ``func`` in a daemon thread (closing its database connection afterwards)."""

    def _run():
        from django.db import close_old_connections

        try:
            func(*args, **kwargs)
        except Exception as error:  # a mail must never take anything else down
            logger.error("Background mail %s failed: %s", getattr(func, "__name__", func), error)
        finally:
            close_old_connections()

    threading.Thread(target=_run, daemon=True).start()


def send_branded_mail(subject, content_html, to, *, company=None, tag_label="", team_label=""):
    """Send one letterhead mail. Returns True if it went out; never raises."""
    if not to:
        return False
    try:
        message = build_letterhead_email(
            subject=subject,
            content_html=content_html,
            to=to,
            tag_label=tag_label,
            team_label=team_label,
            company=company,
            from_email=ConfiguredEmailBackend().dynamic_from_email_with_display_name,
        )
        return bool(message.send())
    except Exception as error:
        logger.error("Mail %r not sent to %s: %s", subject, to, error)
        return False


def paragraph(text_html, *, size=14, color="#444444"):
    """A body paragraph. ``text_html`` must already be escaped / trusted."""
    return f'<p style="margin:0 0 16px;font-size:{size}px;color:{color};line-height:1.7;">{text_html}</p>'


def greeting(name):
    return paragraph(f"Hi <strong>{escape(name)}</strong>,", size=15, color="#2d2d2d")


def info_card(rows, *, accent="#3b6fd4"):
    """A boxed list of ``(label, value)``; both are escaped here."""
    body = "".join(
        f'<p style="margin:0 0 8px;font-size:13px;color:#555555;"><strong>{escape(str(label))}:</strong> {escape(str(value))}</p>'
        for label, value in rows
        if value not in (None, "")
    )
    return (
        f'<div style="margin:20px 0;padding:18px 20px;background:#f7f9fc;border-left:4px solid {accent};'
        f'border-radius:8px;">{body}</div>'
    )


def action_button(link, label):
    return (
        '<div style="text-align:center;margin:0 0 24px;">'
        f'<a href="{escape(link)}" style="display:inline-block;padding:12px 28px;background:#0f6e5c;color:#ffffff;'
        f'font-size:14px;font-weight:600;text-decoration:none;border-radius:6px;">{escape(label)}</a></div>'
    )
