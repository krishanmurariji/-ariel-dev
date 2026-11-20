"""
vertex/forms_expenses.py - expense, decision, invoice and settings forms.
"""

import os
from ipaddress import ip_address
from urllib.parse import urlparse

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from vertex.forms import MAX_ATTENDEES, ModelForm, cap_text, clean_email_list
from vertex.models import Expense, ExpenseSetting

TITLE_LIMIT = 120
DESCRIPTION_LIMIT = 500
VENDOR_LIMIT = 120
NOTE_LIMIT = 500
INVOICE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
INVOICE_MAX_BYTES = 10 * 1000 * 1000


def validate_invoice(upload):
    """An invoice is a PDF or a picture, not empty and at most 10 MB; raises a form error otherwise."""
    if upload is None:
        return upload
    if upload.size == 0:
        raise forms.ValidationError(_("The file is empty."))
    if upload.size > INVOICE_MAX_BYTES:
        raise forms.ValidationError(_("The invoice is larger than 10 MB."))
    if os.path.splitext(upload.name)[1].lower() not in INVOICE_EXTENSIONS:
        raise forms.ValidationError(_("Upload a PDF, PNG or JPG file."))
    return upload


class ExpenseForm(ModelForm):
    vm_icon = "wallet-outline"
    vm_hint = "A payment request. An approver decides it when approval is needed; then you mark it paid."
    vm_add_another = False
    cols = {
        "title": 12, "category": 6, "amount": 6, "vendor": 6, "expense_date": 6, "payment_method": 6,
        "invoice_number": 6, "description": 12, "invoice_file": 12, "approval_required": 12,
    }

    invoice_file = forms.FileField(required=False, label=_("Invoice (PDF, PNG or JPG, up to 10 MB)"))

    class Meta:
        model = Expense
        fields = [
            "title", "category", "amount", "vendor", "expense_date", "payment_method", "invoice_number",
            "description", "approval_required",
        ]
        widgets = {
            "expense_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 2}),
            "amount": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
        }
        labels = {"amount": _("Amount (₹)"), "approval_required": _("This needs approval")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cap_text(self, "title", TITLE_LIMIT)
        cap_text(self, "description", DESCRIPTION_LIMIT)
        cap_text(self, "vendor", VENDOR_LIMIT)
        self.fields["expense_date"].initial = timezone.localdate()
        if ExpenseSetting.current().approval_required_by_default:
            # the company rule: nobody can skip approval by unticking the box
            field = self.fields["approval_required"]
            field.initial = True
            field.disabled = True
            field.help_text = _("Company rule: every expense needs approval.")
        else:
            self.fields["approval_required"].initial = False

    def clean_title(self):
        value = self.cleaned_data["title"].strip()
        if not value:
            raise forms.ValidationError(_("Give the expense a title."))
        return value

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value is None or value <= 0:
            raise forms.ValidationError(_("The amount must be above zero."))
        return value

    def clean_expense_date(self):
        value = self.cleaned_data["expense_date"]
        if value > timezone.localdate():
            raise forms.ValidationError(_("The expense date cannot be in the future."))
        return value

    def clean_invoice_file(self):
        return validate_invoice(self.cleaned_data.get("invoice_file"))


class DecisionForm(forms.Form):
    vm_icon = "checkmark-done-outline"
    vm_hint = "Add a note if it helps."
    vm_add_another = False
    cols = {"note": 12}
    verbose_name = _("Approve expense")

    note = forms.CharField(
        label=_("Note (optional)"), required=False, max_length=NOTE_LIMIT,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": NOTE_LIMIT, "class": "oh-input w-100"}),
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance


class RejectForm(DecisionForm):
    vm_icon = "close-circle-outline"
    vm_hint = "The person who raised it is told why."
    verbose_name = _("Reject expense")

    note = forms.CharField(
        label=_("Why is it rejected?"), required=True, max_length=NOTE_LIMIT,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": NOTE_LIMIT, "class": "oh-input w-100"}),
    )

    def clean_note(self):
        value = self.cleaned_data["note"].strip()
        if not value:
            raise forms.ValidationError(_("Give a reason."))
        return value


class InvoiceForm(forms.Form):
    vm_icon = "document-attach-outline"
    vm_hint = "A PDF, PNG or JPG, up to 10 MB. A new file replaces the old one."
    vm_add_another = False
    cols = {"invoice_file": 12}
    verbose_name = _("Upload invoice")

    invoice_file = forms.FileField(label=_("Invoice"), required=True)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance

    def clean_invoice_file(self):
        return validate_invoice(self.cleaned_data["invoice_file"])


class SettingsForm(ModelForm):
    verbose_name = _("Expense settings")
    vm_icon = "settings-outline"
    vm_hint = "The approval rule and who gets the daily (evening) and weekly (Friday) summaries."
    vm_add_another = False
    cols = {"approval_required_by_default": 12, "daily_recipients": 12, "weekly_recipients": 12, "teams_webhook_url": 12}

    class Meta:
        model = ExpenseSetting
        fields = ["approval_required_by_default", "daily_recipients", "weekly_recipients", "teams_webhook_url"]
        widgets = {
            "daily_recipients": forms.Textarea(attrs={"rows": 2}),
            "weekly_recipients": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "approval_required_by_default": _("Every expense needs approval"),
            "teams_webhook_url": _("Teams channel webhook (optional)"),
        }

    def clean_daily_recipients(self):
        return clean_email_list(self.cleaned_data["daily_recipients"], limit=MAX_ATTENDEES)

    def clean_weekly_recipients(self):
        return clean_email_list(self.cleaned_data["weekly_recipients"], limit=MAX_ATTENDEES)

    def clean_teams_webhook_url(self):
        url = self.cleaned_data["teams_webhook_url"].strip()
        if not url:
            return ""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        try:
            ip_address(host)
            literal = True
        except ValueError:
            literal = False
        if parsed.scheme != "https" or literal or host in ("localhost", "") or host.endswith(".local"):
            raise forms.ValidationError(_("Use the https address Teams gave you (not an IP address or localhost)."))
        return url
