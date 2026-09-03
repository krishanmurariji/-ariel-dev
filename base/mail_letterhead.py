"""
base/mail_letterhead.py

Shared "letterhead" wrapper for every outgoing HTML mail in the app: a
company logo header band, a faded watermark of the same logo behind the
body, and a footer with the company name/team signature - one place so
every mail (existing and future) looks like it came from the same
company, instead of each sender hand-rolling its own header/footer.

Two ways in:
- Hardcoded-HTML mail bodies (f-strings, etc.) call `build_letterhead_email`
  directly - it returns a ready-to-send EmailMultiAlternatives.
- Django-template-based mails render their own child template (which
  `{% extends "mail/letterhead_base.html" %}`) via `letterhead_context`
  for the header/footer variables, then call `attach_letterhead_images`
  on whatever EmailMessage they build themselves.

A future mail written either way automatically gets the same letterhead,
without needing to know how the logo/watermark images actually get
attached.
"""

import io
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.translation import gettext_lazy as _

from base.models import Company

logger = logging.getLogger(__name__)

LETTERHEAD_TEMPLATE = "mail/letterhead_base.html"

# The header strip's blue->black gradient bar. Rendered as a real image
# (not a CSS gradient) for the same reason the watermark is an image -
# classic desktop Outlook's Word rendering engine ignores CSS
# background/linear-gradient entirely, but scales an <img> reliably.
LETTERHEAD_GRADIENT_START = "#0B5FCE"
LETTERHEAD_GRADIENT_END = "#03060f"

# CTA buttons and the no-logo text fallback all use this - kept equal to
# the gradient's own blue so a button doesn't clash with the header
# strip above it.
LETTERHEAD_ACCENT = LETTERHEAD_GRADIENT_START
_GRADIENT_CACHE_KEY = "letterhead_gradient_bar:v1"


def resolve_instance_company(instance):
    """
    Best-effort company lookup for an arbitrary model instance (used by
    the generic mail-automation engine, which can run against any model
    an admin picks). Most models here already expose a get_company() or
    a direct company_id FK, but neither is standardized - get_company()
    on some models (e.g. Candidate) returns the company's name string
    rather than the Company instance, so that's guarded against too.
    Returns a Company instance, or None.
    """
    if instance is None:
        return None
    get_company = getattr(instance, "get_company", None)
    if callable(get_company):
        try:
            result = get_company()
        except Exception:
            result = None
        if isinstance(result, Company):
            return result
    company_id = getattr(instance, "company_id", None)
    if isinstance(company_id, Company):
        return company_id
    return None


def resolve_letterhead_company(company=None, employee=None, instance=None):
    """
    Picks the company whose branding a mail should carry: an explicit
    company if given, else the given employee's own company, else a
    best-effort lookup on an arbitrary model instance (see
    resolve_instance_company - for callers, like the automation engine,
    that don't know in advance what kind of instance they have), else
    the org's headquarters, else None (falls back to generic "Horilla"
    branding). Mirrors base.context_processors.white_labelling_company's
    own resolution, just without needing a request/logged-in user - most
    mail recipients here are logged out, or aren't the one being emailed.
    """
    if not getattr(settings, "WHITE_LABELLING", False):
        return None
    if company:
        return company
    if employee:
        try:
            emp_company = employee.get_company()
        except Exception:
            emp_company = None
        if emp_company:
            return emp_company
    if instance is not None:
        instance_company = resolve_instance_company(instance)
        if instance_company:
            return instance_company
    return Company.objects.filter(hq=True).last()


def _load_logo_bytes(company):
    if not company or not company.icon:
        return None
    try:
        with company.icon.open("rb") as f:
            return f.read()
    except Exception as e:
        logger.warning("Letterhead logo read failed for company %s: %s", company, e)
        return None


def _load_watermark_bytes(company):
    """
    A faded (7% opacity) PNG of the company's logo, cached per company +
    icon path so repeat sends (bulk payslip mail, etc.) don't redo the
    image processing every time. Returns None if there's no logo, or PIL
    can't process it (unsupported format, missing file, ...) - callers
    treat that as "skip the watermark", not a failure worth blocking the
    actual mail over.
    """
    if not company or not company.icon:
        return None
    cache_key = f"letterhead_watermark:{company.pk}:{company.icon.name}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    try:
        from PIL import Image

        with company.icon.open("rb") as f:
            source = Image.open(f)
            source.load()
        faded = source.convert("RGBA")
        r, g, b, a = faded.split()
        a = a.point(lambda p: int(p * 0.07))
        faded = Image.merge("RGBA", (r, g, b, a))
        buf = io.BytesIO()
        faded.save(buf, format="PNG")
        data = buf.getvalue()
    except Exception as e:
        logger.warning("Letterhead watermark generation failed for company %s: %s", company, e)
        cache.set(cache_key, False, timeout=60 * 60)
        return None
    cache.set(cache_key, data, timeout=60 * 60 * 24)
    return data


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _load_gradient_bar_bytes():
    """
    A left-to-right blue->black gradient PNG for the header strip, cached
    once (it's a fixed brand element, not per-company) rather than
    regenerated on every send.
    """
    cached = cache.get(_GRADIENT_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from PIL import Image

        width, height = 1200, 12
        start = _hex_to_rgb(LETTERHEAD_GRADIENT_START)
        end = _hex_to_rgb(LETTERHEAD_GRADIENT_END)
        img = Image.new("RGB", (width, height))
        pixels = img.load()
        row = []
        for x in range(width):
            t = x / (width - 1)
            row.append(
                tuple(round(start[i] + (end[i] - start[i]) * t) for i in range(3))
            )
        for y in range(height):
            for x in range(width):
                pixels[x, y] = row[x]
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
    except Exception as e:
        logger.warning("Letterhead gradient bar generation failed: %s", e)
        return None
    cache.set(_GRADIENT_CACHE_KEY, data, timeout=60 * 60 * 24)
    return data


def attach_letterhead_images(message, company):
    """
    Attaches the company logo (cid:letterhead_logo) and its faded
    watermark (cid:letterhead_watermark) as inline images on an
    already-built EmailMessage/EmailMultiAlternatives. CID (not a remote
    URL) so the images show even when a mail client blocks remote image
    loading by default. Returns (has_logo, has_watermark) so the
    caller's rendered HTML matches what actually got attached.
    """
    from email.mime.image import MIMEImage

    message.mixed_subtype = "related"
    has_logo = False
    has_watermark = False

    logo_bytes = _load_logo_bytes(company)
    if logo_bytes:
        try:
            logo_ext = (company.icon.name.rsplit(".", 1)[-1] or "png").lower()
            logo_subtype = "jpeg" if logo_ext in ("jpg", "jpeg") else logo_ext
            logo_img = MIMEImage(logo_bytes, _subtype=logo_subtype)
            logo_img.add_header("Content-ID", "<letterhead_logo>")
            logo_img.add_header("Content-Disposition", "inline")
            message.attach(logo_img)
            has_logo = True
        except Exception as e:
            logger.warning("Letterhead logo attach failed: %s", e)

    watermark_bytes = _load_watermark_bytes(company)
    if watermark_bytes:
        wm_img = MIMEImage(watermark_bytes, _subtype="png")
        wm_img.add_header("Content-ID", "<letterhead_watermark>")
        wm_img.add_header("Content-Disposition", "inline")
        message.attach(wm_img)
        has_watermark = True

    # Fixed brand element, not company-specific - always attach.
    gradient_bytes = _load_gradient_bar_bytes()
    if gradient_bytes:
        gradient_img = MIMEImage(gradient_bytes, _subtype="png")
        gradient_img.add_header("Content-ID", "<letterhead_gradient>")
        gradient_img.add_header("Content-Disposition", "inline")
        message.attach(gradient_img)

    return has_logo, has_watermark


def letterhead_context(company, *, tag_label="", team_label=None, has_logo=False,
                        has_watermark=False, content=""):
    """
    The variables mail/letterhead_base.html (or a template extending it)
    needs. Shared so a Django-template sender and the
    build_letterhead_email() Python-string path stay in sync.
    """
    return {
        "company_name": company.company if company else "Horilla",
        "accent_color": LETTERHEAD_ACCENT,
        "tag_label": tag_label,
        "team_label": team_label or _("Team"),
        "has_logo": has_logo,
        "has_watermark": has_watermark,
        "content": content,
    }


def render_letterhead_content(content_html, *, tag_label="", team_label=None,
                               company=None, employee=None, instance=None):
    """
    Wraps content_html in the letterhead and returns just the rendered
    HTML string, for callers that build their own EmailMessage (cc,
    attachments, etc.) instead of using build_letterhead_email - they
    still need to call attach_letterhead_images(message, company)
    themselves afterward. Returns (html, company, has_logo) so the
    caller can pass the same company/has_logo along to that call.
    """
    resolved_company = resolve_letterhead_company(company=company, employee=employee, instance=instance)
    has_logo = bool(resolved_company and resolved_company.icon)
    context = letterhead_context(
        resolved_company, tag_label=tag_label, team_label=team_label,
        has_logo=has_logo, has_watermark=has_logo, content=content_html,
    )
    return render_to_string(LETTERHEAD_TEMPLATE, context), resolved_company, has_logo


def build_letterhead_email(subject, content_html, to, *, tag_label="", team_label=None,
                            company=None, employee=None, from_email=None, reply_to=None,
                            plain_text=None, connection=None):
    """
    Builds a ready-to-send EmailMultiAlternatives with the letterhead
    wrapper as its HTML body, logo/watermark attached as CID images -
    the one call a hardcoded-HTML mail body should make instead of
    building its own EmailMessage and header/footer markup from scratch.
    """
    resolved_company = resolve_letterhead_company(company=company, employee=employee)
    assumed_has_logo = bool(resolved_company and resolved_company.icon)

    context = letterhead_context(
        resolved_company, tag_label=tag_label, team_label=team_label,
        has_logo=assumed_has_logo, has_watermark=assumed_has_logo, content=content_html,
    )
    html_body = render_to_string(LETTERHEAD_TEMPLATE, context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=plain_text or strip_tags(content_html),
        from_email=from_email,
        to=[to] if isinstance(to, str) else list(to),
        reply_to=reply_to,
        connection=connection,
    )
    message.attach_alternative(html_body, "text/html")

    has_logo, has_watermark = attach_letterhead_images(message, resolved_company)
    if (has_logo, has_watermark) != (assumed_has_logo, assumed_has_logo):
        # Attaching failed after the HTML already assumed success (e.g. the
        # logo file is missing on disk) - cheap enough to re-render once
        # with the real outcome rather than ship a broken image icon.
        context.update(has_logo=has_logo, has_watermark=has_watermark)
        html_body = render_to_string(LETTERHEAD_TEMPLATE, context)
        message.alternatives = [(html_body, "text/html")]

    return message
