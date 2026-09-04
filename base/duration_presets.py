"""
duration_presets.py

CRUD for DurationPreset - a reusable, admin-configurable duration (e.g.
"1 Week", "Quarter") shared by the Feedback Period quick-picker and the
Task Template Default Due Date picker.
"""

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from base.forms import DurationPresetForm
from base.models import DurationPreset
from horilla.decorators import login_required, permission_required


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("base.view_durationpreset"), name="dispatch")
class DurationPresetListView(View):
    """
    Settings page listing every configured duration preset.
    """

    def get(self, request):
        presets = DurationPreset.objects.all()
        return render(
            request, "base/duration_presets/list.html", {"presets": presets}
        )


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("base.add_durationpreset"), name="dispatch")
class DurationPresetFormView(View):
    """
    Create/edit a duration preset. Also usable from the quick-picker
    dropdowns (Feedback, Task Template) as an "Add new preset" AJAX
    submit - the same POST handling returns JSON there instead of
    redirecting, based on the X-Requested-With header.
    """

    def get(self, request, pk=None):
        instance = DurationPreset.objects.filter(pk=pk).first() if pk else None
        form = DurationPresetForm(instance=instance)
        return render(
            request,
            "base/duration_presets/form.html",
            {"form": form, "instance": instance},
        )

    def post(self, request, pk=None):
        instance = DurationPreset.objects.filter(pk=pk).first() if pk else None
        form = DurationPresetForm(request.POST, instance=instance)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if form.is_valid():
            preset = form.save()
            if is_ajax:
                return JsonResponse(
                    {
                        "id": preset.id,
                        "label": preset.label,
                        "days": preset.days_count(),
                    }
                )
            messages.success(request, _("Duration preset saved"))
            return redirect("duration-preset-list")
        if is_ajax:
            return JsonResponse({"errors": form.errors.as_json()}, status=400)
        return render(
            request,
            "base/duration_presets/form.html",
            {"form": form, "instance": instance},
        )


@login_required
@permission_required("base.delete_durationpreset")
def duration_preset_delete(request, pk):
    get_object_or_404(DurationPreset, pk=pk).delete()
    messages.success(request, _("Duration preset deleted"))
    return redirect("duration-preset-list")
