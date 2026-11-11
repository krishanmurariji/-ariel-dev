"""
vertex/filters.py
"""

import django_filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from horilla.filters import HorillaFilterSet
from vertex.models import Project, ResourceRequest, StatusUpdate


class VertexProjectFilter(HorillaFilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Project
        fields = ["status", "health", "priority"]

    start_from = django_filters.DateFilter(
        field_name="start_date",
        lookup_expr="gte",
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("Start From"),
    )
    end_till = django_filters.DateFilter(
        field_name="expected_end_date",
        lookup_expr="lte",
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("End Till"),
    )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(code__icontains=value)
            | Q(client_name__icontains=value)
            | Q(business_owner__icontains=value)
        )


class StatusUpdateFilter(HorillaFilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = StatusUpdate
        fields = ["project", "status"]

    date_from = django_filters.DateFilter(
        field_name="update_date",
        lookup_expr="gte",
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("From"),
    )
    date_to = django_filters.DateFilter(
        field_name="update_date",
        lookup_expr="lte",
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("To"),
    )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(work_completed__icontains=value)
            | Q(blockers__icontains=value)
            | Q(project__name__icontains=value)
            | Q(project__code__icontains=value)
        )


class ResourceRequestFilter(HorillaFilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = ResourceRequest
        fields = ["project", "status", "priority"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(role_title__icontains=value)
            | Q(skills__icontains=value)
            | Q(project__name__icontains=value)
            | Q(project__code__icontains=value)
        )
