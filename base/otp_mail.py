"""
base/otp_mail.py

Sends the one-time login code by email. Shared by the web two-factor page
(code kept in the session) and the API login (code kept in the cache), so the
mail itself is built in exactly one place.
"""

import threading

from django.utils.translation import gettext as _

from base.backends import ConfiguredEmailBackend
from base.mail_letterhead import build_letterhead_email


def send_otp_email(employee, otp_code):
    """Email ``otp_code`` to ``employee`` in the background."""
    email = employee.get_mail()
    display_email_name = ConfiguredEmailBackend().dynamic_from_email_with_display_name

    content_html = (
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">'
        + str(_("Your one-time verification code is:"))
        + "</p>"
        + f'<p style="margin:0 0 16px;font-size:32px;font-weight:700;letter-spacing:4px;color:#2d2d2d;">{otp_code}</p>'
        + '<p style="margin:0;font-size:13px;color:#888888;">'
        + str(_("If you didn't request this code, you can safely ignore this email."))
        + "</p>"
    )
    message = build_letterhead_email(
        subject=_("Your OTP Code"),
        content_html=content_html,
        to=email,
        tag_label=_("Security"),
        team_label=_("IT Team"),
        employee=employee,
        from_email=display_email_name,
        plain_text=_("Your OTP code is %(otp_code)s") % {"otp_code": otp_code},
    )
    message.sensitive = True  # keep the code out of the mail log
    threading.Thread(target=message.send).start()
