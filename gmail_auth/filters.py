"""
gmail_auth/filters.py
"""

import django_filters

from gmail_auth import models


class GoogleApiFilter(django_filters.FilterSet):
    """
    GoogleApiFilter
    """

    search = django_filters.CharFilter(
        field_name="google_email", lookup_expr="icontains"
    )

    class Meta:
        """
        Meta class for additional options"""

        fields = [
            "search",
        ]
        model = models.GoogleApi
