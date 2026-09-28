"""
base/html_sanitizer.py

One allow-list cleaner for rich text that a user types into the editor and
that is later shown to *other* users with ``|safe`` (announcements today).

Anything not explicitly allowed is removed, rather than trying to spot bad
things: a <script> block is deleted along with its contents; every attribute
that isn't listed (all ``on...=`` event handlers, ``class``, ``id``, ...)
is dropped; links may only point at http, https, mailto, tel or a relative
address (never ``javascript:``); an image may only use an http(s)/relative
address or an inline png/jpeg/gif/webp ``data:`` URI (what the editor embeds
when a picture is pasted in - never svg, which can carry script); inline
``style`` is limited to plain presentation properties (never ``position``,
``background`` with a url, ``expression()``, ``behavior`` ...).

Deliberately imports nothing from the project so both models and forms can
use it without a circular import.
"""

import re
from urllib.parse import urlparse

import bleach
from bleach.css_sanitizer import CSSSanitizer

ALLOWED_TAGS = frozenset(
    {
        "a", "abbr", "b", "blockquote", "br", "caption", "code", "col",
        "colgroup", "del", "div", "em", "font", "h1", "h2", "h3", "h4", "h5",
        "h6", "hr", "i", "img", "ins", "li", "mark", "ol", "p", "pre", "s",
        "small", "span", "strike", "strong", "sub", "sup", "table", "tbody",
        "td", "tfoot", "th", "thead", "tr", "u", "ul",
    }
)  # fmt: skip

ALLOWED_CSS_PROPERTIES = [
    "background-color", "border", "border-bottom", "border-collapse",
    "border-color", "border-left", "border-radius", "border-right",
    "border-spacing", "border-style", "border-top", "border-width", "color",
    "direction", "font-family", "font-size", "font-style", "font-weight",
    "height", "letter-spacing", "line-height", "list-style-type", "margin",
    "margin-bottom", "margin-left", "margin-right", "margin-top",
    "max-height", "max-width", "min-height", "min-width", "padding",
    "padding-bottom", "padding-left", "padding-right", "padding-top",
    "text-align", "text-decoration", "text-indent", "text-transform",
    "vertical-align", "white-space", "width", "word-break",
]  # fmt: skip

_CSS = CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES)

# Attributes allowed on any allowed tag.
_GENERIC_ATTRS = {
    "style", "title", "align", "valign", "dir", "width", "height", "colspan",
    "rowspan", "border", "cellpadding", "cellspacing", "color", "face", "size",
    "start", "type", "lang",
}  # fmt: skip

_DATA_IMAGE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I
)
_SCRIPT_OR_STYLE_BLOCK = re.compile(
    r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>", re.I | re.S
)
_INVISIBLE = re.compile(r"[\x00-\x20 ​-‏  ﻿]")
# A style attribute containing any of these is dropped whole rather than
# repaired: none is needed for plain formatting, and the CSS cleaner leaves
# function values such as expression(...) untouched on an allowed property.
_BAD_STYLE = re.compile(
    r"expression|url\s*\(|javascript|vbscript|@import|behavior|binding|\\|/\*",
    re.I,
)


def _scheme(value):
    # Browsers ignore whitespace/control characters inside a URL's scheme
    # ("java\tscript:"), so compare against the value with all of them removed.
    return urlparse(_INVISIBLE.sub("", value or "")).scheme.lower()


def _allowed_attribute(tag, name, value):
    if name == "style":
        return not _BAD_STYLE.search(value or "")
    if name in _GENERIC_ATTRS:
        return True
    if tag == "a":
        if name == "href":
            return _scheme(value) in {"", "http", "https", "mailto", "tel"}
        if name == "target":
            return value in {"_blank", "_self"}
        if name == "rel":
            return True
    if tag == "img":
        if name == "alt":
            return True
        if name == "src":
            if _scheme(value) in {"", "http", "https"}:
                return True
            return bool(_DATA_IMAGE.match(_INVISIBLE.sub("", value or "")))
    return False


def sanitize_html(html):
    """Return ``html`` with everything not on the allow-list removed."""
    if not html:
        return html
    # Drop <script>/<style> together with what's inside them, so their code
    # doesn't linger as visible text once the tags themselves are stripped.
    html = _SCRIPT_OR_STYLE_BLOCK.sub("", str(html))
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=_allowed_attribute,
        # "data" is allowed at this level only so inline images survive;
        # _allowed_attribute decides where a data: URI is actually acceptable
        # (img src only, raster formats only - never a link).
        protocols=["http", "https", "mailto", "tel", "data"],
        css_sanitizer=_CSS,
        strip=True,
        strip_comments=True,
    )
