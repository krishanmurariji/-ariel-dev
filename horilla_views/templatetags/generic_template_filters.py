"""
horilla_views/templatetags/generic_template_filters.py

This module is used to write custom template filters.

"""

import datetime
import functools
import re
import types

from django import template
from django.conf import settings
from django.contrib.auth.context_processors import PermWrapper
from django.db.models.utils import AltersData
from django.template.defaultfilters import register
from django.utils.html import conditional_escape
from django.utils.functional import Promise
from django.utils.safestring import SafeData, mark_safe
from django.utils.translation import gettext_lazy as _

from base.html_sanitizer import sanitize_html
from horilla.config import import_method
from horilla.horilla_middlewares import _thread_locals

register = template.Library()


numeric_test = re.compile(r"^\d+$")

date_format_mapping = {
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "YYYY/MM/DD": "%Y/%m/%d",
    "MMMM D, YYYY": "%B %d, %Y",
    "DD MMMM, YYYY": "%d %B, %Y",
    "MMM. D, YYYY": "%b. %d, %Y",
    "D MMM. YYYY": "%d %b. %Y",
    "dddd, MMMM D, YYYY": "%A, %B %d, %Y",
}

time_format_mapping = {
    "hh:mm A": "%I:%M %p",
    "HH:mm": "%H:%M",
}


@register.filter(name="selected_format")
def selected_format(date, company: object = None) -> str:
    if not isinstance(date, (datetime.date, datetime.time)):
        try:
            date = datetime.datetime.fromisoformat(date)
        except:
            return date
    if company and (company.date_format or company.time_format):
        if isinstance(date, datetime.date):
            format = company.date_format if company.date_format else "MMM. D, YYYY"
            date_format_mapping.get(format)
            return date.strftime(date_format_mapping[format])
        elif isinstance(date, datetime.time):
            format = company.time_format if company.time_format else "hh:mm A"
            return date.strftime(time_format_mapping[format])
    return date


@register.filter(name="getattribute")
def getattribute(value, attr: str):
    """
    Gets an attribute of an object dynamically from a string name
    """
    result = ""
    attrs = attr.split("__")
    for attr in attrs:
        if isinstance(
            value,
            AltersData,
        ) and hasattr(value, "through"):
            result = []
            queryset = value.all()
            for record in queryset:
                result.append(getattribute(record, attr))
        elif hasattr(value, str(attr)):
            result = getattr(value, attr)
            if isinstance(result, (types.MethodType, functools.partial)):
                result = result()
            value = result
        else:
            return getattr(value, attr, "")

    # Python code we need raw bool values, not "Yes"/"No"
    # if isinstance(result, bool):
    #     return _("Yes") if result else _("No")

    return result


@register.filter(name="cell_value")
def cell_value(value, attr: str):
    """
    Same lookup as ``getattribute``, for table cells that are printed as HTML.

    The result of calling a *method* on the record (for example
    ``get_status_col``) is code-built HTML and is marked trusted. Anything
    else is plain stored data - a name, a title, a description someone typed -
    and is left unmarked so ``safe_cell`` cleans it.
    """
    result = ""
    for part in attr.split("__"):
        if isinstance(value, AltersData) and hasattr(value, "through"):
            result = [cell_value(record, part) for record in value.all()]
        elif hasattr(value, str(part)):
            result = getattr(value, part)
            if isinstance(result, (types.MethodType, functools.partial)):
                result = result()
                # built by the method; translated text built with format_lazy is a lazy
                # proxy, not a str, so it has to be recognised here or its tags show as text
                if isinstance(result, (str, Promise)) and "<" in str(result):
                    result = mark_safe(str(result))
            value = result
        else:
            return getattr(value, part, "")
    return result


@register.filter(name="safe_cell")
def safe_cell(value):
    """
    Final step for a table cell (use instead of ``|safe``): trusted HTML from
    ``cell_value`` passes through; stored text is cleaned so formatting a user
    typed still shows, but script, event handlers and the like are removed.
    """
    if isinstance(value, SafeData):
        return value
    if isinstance(value, str):
        return mark_safe(sanitize_html(value))
    return conditional_escape(value)


@register.filter(name="format_attrs")
def format_attrs(string: str, instance: object):
    """
    ``format`` for strings that are printed as raw HTML (``|safe``), e.g. the
    ``attrs`` of a row, action or card. The template string itself is written
    by the developer and kept as is; the record's *data* filled into it is
    escaped so a name or title cannot close the attribute and add its own.
    Values that come from calling a method are code-built and left alone.
    """
    string = str(string)
    placeholders = re.findall(r"{([^}]*)}", string)
    if not placeholders:
        return mark_safe(string)
    base = instance
    context = {}
    for placeholder in placeholders:
        instance = base
        from_method = False
        for attr in placeholder.split("__"):
            from_method = False
            if (
                attr.startswith("get_")
                and attr.endswith("_display")
                and callable(getattr(instance, attr, None))
            ):
                value = getattr(instance, attr)()
                from_method = True
            else:
                value = getattr(instance, attr, "")
            if isinstance(value, types.MethodType):
                value = value()
                from_method = True
            instance = value
        context[placeholder] = (
            value if from_method or isinstance(value, SafeData) else conditional_escape(value)
        )
    return mark_safe(string.format(**context))


@register.filter(name="format")
def format(string: str, instance: object):
    """
    format
    """
    string = str(string)
    attr_placeholder_regex = r"{([^}]*)}"
    attr_placeholders = re.findall(attr_placeholder_regex, string)

    if not attr_placeholders:
        return string
    flag = instance
    format_context = {}
    for attr_placeholder in attr_placeholders:
        attr_name: str = attr_placeholder
        attrs = attr_name.split("__")
        for attr in attrs:
            if (
                attr.startswith("get_")
                and attr.endswith("_display")
                and callable(getattr(instance, attr, None))
            ):  # 874
                value = getattr(instance, attr)()
            else:
                value = getattr(instance, attr, "")

            if isinstance(value, types.MethodType):
                value = value()
            instance = value
            format_context[attr_name] = value
        instance = flag
    formatted_string = string.format(**format_context)

    return formatted_string


@register.filter("accessibility")
def accessibility(method: str, instance=None):
    if method:
        request = getattr(_thread_locals, "request")
        method = import_method(method)
        return method(
            request,
            instance,
            PermWrapper(request.user),
        )
    return True


@register.filter("col")
def col(field: object):
    """
    Method to get field col sepration
    """
    field_name = field.name
    cols = getattr(field.form, "cols", {})
    return cols.get(field_name, 6)


@register.filter("get_item")
def get_item(dictionary: dict, key: str):
    """
    get_item method to access from dictionary
    """
    if dictionary:
        return dictionary.get(key, "")
    return ""


@register.filter("get_id")
def get_id(string: str):
    """
    Generate target/id for the generic delete summary
    """
    return string.split("-")[0].lower().replace(" ", "")


@register.filter
def is_image_file(filename):
    """
    Django template filter to check if a given filename is an image file.
    """
    return filename.lower().endswith((".png", ".jpg", ".jpeg", ".svg"))
