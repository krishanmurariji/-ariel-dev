from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm, forms
from horilla_meet.models import *
from recruitment.models import Candidate


class GoogleCloudCredentialForm(ModelForm):
    class Meta:
        model = GoogleCloudCredential
        fields = "__all__"
        widgets = {
            "client_secret": forms.PasswordInput(render_value=False),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client_secret"].widget.attrs.update(
            {"class": "oh-input w-100 form-control"}
        )
        if self.instance.pk:
            self.fields["client_secret"].required = False
            self.fields["client_secret"].help_text = _(
                "Leave blank to keep the current secret unchanged."
            )

    def clean_client_secret(self):
        value = self.cleaned_data.get("client_secret")
        if not value and self.instance.pk:
            return self.instance.client_secret
        return value


class GoogleMeetingForm(ModelForm):
    attendees = forms.MultipleChoiceField(choices=[], widget=forms.SelectMultiple)

    class Meta:
        model = GoogleMeeting
        fields = ["title", "description", "start_time", "duration", "attendees"]
        widgets = {
            "start_time": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control oh-input"}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        employees = [
            (emp.get_mail(), emp.get_full_name()) for emp in Employee.objects.all()
        ]
        candidates = [(cand.get_mail(), cand.name) for cand in Candidate.objects.all()]

        self.fields["attendees"].choices = [
            ("Employees", employees),
            ("Candidates", candidates),
        ]
