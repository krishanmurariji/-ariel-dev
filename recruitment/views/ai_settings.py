"""
recruitment/views/ai_settings.py

Settings > Integrations > AI Settings: lets an admin pick which LLM
provider recruitment's AI features use (Ollama, local, or OpenAI, cloud)
and enter the credentials for it. Read by recruitment/ai_client.py at
call time - nothing else should read AISettings directly.
"""

from django.contrib import messages
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from recruitment.forms import AISettingsForm
from recruitment.models import AISettings


@login_required
@permission_required("recruitment.change_aisettings")
def ai_settings_view(request):
    """
    Single-row settings form (see AISettings.get_solo) - always edits the
    same instance rather than being a create/list/update trio.
    """
    ai_settings = AISettings.get_solo()
    # Captured before the form ever touches ai_settings - form.save() below
    # mutates ai_settings in place (it's bound as instance=ai_settings), so
    # reading ai_settings.openai_api_key after that point would just be
    # reading back whatever the form already overwrote it with.
    previous_api_key = ai_settings.openai_api_key

    if request.method == "POST":
        form = AISettingsForm(request.POST, instance=ai_settings)
        if form.is_valid():
            instance = form.save(commit=False)
            if not form.cleaned_data.get("openai_api_key") and previous_api_key:
                # Blank submission means "leave it as-is" - the field is a
                # PasswordInput that never echoes the stored key back into
                # the page, so an empty value here is never a deliberate
                # clear, always just an untouched field.
                instance.openai_api_key = previous_api_key
            instance.save()
            messages.success(request, _("AI settings updated successfully."))
            ai_settings = instance
            form = AISettingsForm(instance=ai_settings)
    else:
        form = AISettingsForm(instance=ai_settings)

    return render(
        request,
        "recruitment/ai_settings.html",
        {"form": form, "has_openai_key": bool(ai_settings.openai_api_key)},
    )
