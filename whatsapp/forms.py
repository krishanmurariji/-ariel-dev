# forms.py
from typing import Any

from django import forms
from django.forms import ValidationError
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from whatsapp.models import WhatsappCredientials


class WhatsappForm(ModelForm):
    cols = {"meta_token": 12}

    class Meta:
        model = WhatsappCredientials
        fields = "__all__"
        exclude = ["is_active", "created_templates"]
        widgets = {
            "meta_token": forms.PasswordInput(render_value=False),
            "meta_webhook_token": forms.PasswordInput(render_value=False),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("meta_token", "meta_webhook_token"):
            self.fields[field_name].widget.attrs.update(
                {"class": "oh-input w-100 form-control"}
            )
            if self.instance.pk:
                self.fields[field_name].required = False
                self.fields[field_name].help_text = _(
                    "Leave blank to keep the current value unchanged."
                )

    def clean_meta_token(self):
        value = self.cleaned_data.get("meta_token")
        if not value and self.instance.pk:
            return self.instance.meta_token
        return value

    def clean_meta_webhook_token(self):
        value = self.cleaned_data.get("meta_webhook_token")
        if not value and self.instance.pk:
            return self.instance.meta_webhook_token
        return value

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("horilla_form.html", context)
        return table_html

    def clean(self):
        cleaned_data = super().clean()
        companies = cleaned_data.get("company_id")
        is_primary = cleaned_data.get("is_primary")

        if companies:
            for company in companies:
                existing_primary = WhatsappCredientials.objects.filter(
                    company_id=company, is_primary=True
                ).exclude(id=self.instance.id)

                if is_primary:
                    if existing_primary.exists():
                        raise ValidationError(
                            f"Company '{company.company}' already has a primary credential."
                        )
                else:
                    if not existing_primary.exists():
                        cleaned_data["is_primary"] = True

        return cleaned_data
