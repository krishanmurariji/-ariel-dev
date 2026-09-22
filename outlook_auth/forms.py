"""
outlook_auth/forms.py
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from outlook_auth import models


class OutlookServerForm(ModelForm):
    """
    OutlookServerForm
    """

    class Meta:
        """
        Meta
        """

        model = models.AzureApi
        fields = "__all__"
        widgets = {
            "outlook_client_secret": forms.PasswordInput(render_value=False),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # PasswordInput isn't one of base.forms.ModelForm's styled widget
        # types, so it needs the same classes applied by hand.
        self.fields["outlook_client_secret"].widget.attrs.update(
            {"class": "oh-input w-100 form-control"}
        )
        if self.instance.pk:
            self.fields["outlook_client_secret"].required = False
            self.fields["outlook_client_secret"].help_text = _(
                "Leave blank to keep the current secret unchanged."
            )

    def clean_outlook_client_secret(self):
        value = self.cleaned_data.get("outlook_client_secret")
        if not value and self.instance.pk:
            return self.instance.outlook_client_secret
        return value
