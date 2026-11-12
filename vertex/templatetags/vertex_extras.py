"""
vertex/templatetags/vertex_extras.py
"""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_BOLD = re.compile(r"\*\*(.+?)\*\*")


@register.filter
def minutes_html(text):
    """
    Show generated minutes (a small markdown subset: # / ## headings, **bold**, - bullets)
    as formatted HTML. The text is escaped first, so notes can never inject markup.
    """
    out, in_list = [], False
    for raw in (text or "").splitlines():
        line = escape(raw.rstrip())
        stripped = line.strip()
        if stripped.startswith("- "):
            if not in_list:
                out.append('<ul class="vx-min-list">')
                in_list = True
            out.append(f"<li>{_BOLD.sub(r'<b>\1</b>', stripped[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not stripped:
            continue
        if stripped.startswith("## "):
            out.append(f'<h4 class="vx-min-h2">{stripped[3:]}</h4>')
        elif stripped.startswith("# "):
            out.append(f'<h3 class="vx-min-h1">{stripped[2:]}</h3>')
        else:
            out.append(f"<p class=\"vx-min-p\">{_BOLD.sub(r'<b>\1</b>', stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return mark_safe("\n".join(out))
