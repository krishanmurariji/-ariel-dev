"""
horilla/post_only.py

``@post_only`` for views that change data.

A view that writes on GET can be triggered from another site by a single link a
logged-in person clicks, because Django does not CSRF-check GET. ``post_only``
answers anything but POST with 405; POST is covered by the CSRF middleware.

Many of these views used to be called with GET and read their values from
``request.GET``. When a page now calls them with POST, htmx puts an element's own
value (a toggle's ``name=value``, say) in the request *body*. So that those views
keep working unchanged, values that arrive in the body are also made readable
through ``request.GET``. A value in the URL's query string always wins, and the
CSRF token is never copied (it would otherwise end up in redirect URLs built from
``request.GET.urlencode()``).
"""

from functools import wraps

from django.http import HttpResponseNotAllowed

_NOT_COPIED = {"csrfmiddlewaretoken"}


def post_only(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        merged = request.GET.copy()
        for key in request.POST:
            if key not in _NOT_COPIED and key not in merged:
                merged.setlist(key, request.POST.getlist(key))
        request.GET = merged
        return view(request, *args, **kwargs)

    return wrapped
