import os
import tempfile
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from horilla_backup.gdrive import authenticate

from .models import GoogleDriveBackup, LocalBackup


class LocalBackupSetupForm(ModelForm):
    backup_db = forms.BooleanField(
        required=False, help_text=_("Enable to backup database to server.")
    )
    backup_media = forms.BooleanField(
        required=False, help_text=_("Enable to backup all media files to server.")
    )
    interval = forms.BooleanField(
        required=False,
        help_text=_("Enable to automate the backup in a period of seconds."),
    )
    fixed = forms.BooleanField(
        required=False, help_text=_("Enable to automate the backup in a fixed time.")
    )

    class Meta:
        model = LocalBackup
        exclude = ["active"]

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def clean(self):
        cleaned_data = super().clean()
        backup_db = cleaned_data.get("backup_db")
        backup_media = cleaned_data.get("backup_media")
        interval = cleaned_data.get("interval")
        fixed = cleaned_data.get("fixed")
        seconds = cleaned_data.get("seconds")
        hour = cleaned_data.get("hour")
        minute = cleaned_data.get("minute")
        backup_path = cleaned_data.get("backup_path")
        path = Path(backup_path)
        if not path.exists():
            raise ValidationError({"backup_path": _("The directory does not exist.")})
        if backup_db == False and backup_media == False:
            raise forms.ValidationError("Please select any backup option.")
        if interval == False and fixed == False:
            raise forms.ValidationError("Please select any backup automate option.")
        if interval == True and seconds == None:
            raise ValidationError({"seconds": _("This field is required.")})
        if fixed == True and hour == None:
            raise ValidationError({"hour": _("This field is required.")})
        if seconds:
            if seconds < 0:
                raise ValidationError(
                    {"seconds": _("Negative value is not accepatable.")}
                )
        if hour:
            if hour < 0 or hour > 24:
                raise ValidationError({"hour": _("Enter a hour between 0 to 24.")})
        if minute:
            if minute < 0 or minute > 60:
                raise ValidationError({"minute": _("Enter a minute between 0 to 60.")})
        return cleaned_data


class GdriveBackupSetupForm(ModelForm):
    verbose_name = "Gdrive Backup"
    backup_db = forms.BooleanField(
        required=False,
        label="Backup DB",
        help_text=_("Enable to backup database to Gdrive"),
    )
    backup_media = forms.BooleanField(
        required=False,
        label="Backup Media",
        help_text=_("Enable to backup all media files to Gdrive"),
    )
    interval = forms.BooleanField(
        required=False,
        help_text=_("Enable to automate the backup in a period of seconds."),
    )
    fixed = forms.BooleanField(
        required=False, help_text=_("Enable to automate the backup in a fixed time.")
    )

    class Meta:
        model = GoogleDriveBackup
        exclude = ["active"]

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def clean(self):
        cleaned_data = super().clean()
        backup_db = cleaned_data.get("backup_db")
        backup_media = cleaned_data.get("backup_media")
        interval = cleaned_data.get("interval")
        fixed = cleaned_data.get("fixed")
        seconds = cleaned_data.get("seconds")
        hour = cleaned_data.get("hour")
        minute = cleaned_data.get("minute")
        service_account_file = cleaned_data.get("service_account_file")

        # Get instance if updating
        instance = self.instance if hasattr(self, "instance") else None

        # Only validate file if it's provided (new file upload) or if creating new instance
        if service_account_file:
            try:
                # Read file content from InMemoryUploadedFile
                file_data = service_account_file.read()

                # Always write to temp file for validation (because .path isn't supported)
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".json", mode="w+b"
                ) as tmp_file:
                    tmp_file.write(file_data)
                    tmp_file.flush()
                    temp_path = tmp_file.name

                # Validate service account credentials file format
                import json

                with open(temp_path, "r") as f:
                    service_account_config = json.load(f)

                # Check it's a valid Google service account key file - these
                # always have "type": "service_account" plus these two, at
                # minimum, per Google's own key format.
                if service_account_config.get("type") != "service_account":
                    raise ValueError(
                        "File must be a Google service account key "
                        "(missing/incorrect 'type': 'service_account')."
                    )

                if "client_email" not in service_account_config:
                    raise ValueError(
                        "Service account file must contain 'client_email'."
                    )

                if "private_key" not in service_account_config:
                    raise ValueError(
                        "Service account file must contain 'private_key'."
                    )

                # Clean up temp file
                os.remove(temp_path)

            except json.JSONDecodeError:
                raise forms.ValidationError(
                    "Please provide a valid service account file (must be valid JSON)."
                )
            except Exception as e:
                raise forms.ValidationError(
                    f"Please provide a valid service account file. Error: {str(e)}"
                )
        elif not instance or not instance.pk:
            # If creating new instance and no file provided, raise error
            raise forms.ValidationError(
                "Please provide a valid service account file."
            )
        if backup_db == False and backup_media == False:
            raise forms.ValidationError("Please select any backup option.")
        if interval == False and fixed == False:
            raise forms.ValidationError("Please select any backup automate option.")
        if interval == True and seconds == None:
            raise ValidationError({"seconds": _("This field is required.")})
        if fixed == True and hour == None:
            raise ValidationError({"hour": _("This field is required.")})
        if seconds:
            if seconds < 0:
                raise ValidationError(
                    {"seconds": _("Negative value is not accepatable.")}
                )
        if hour:
            if hour < 0 or hour > 24:
                raise ValidationError({"hour": _("Enter a hour between 0 to 24.")})
        if minute:
            if minute < 0 or minute > 60:
                raise ValidationError({"minute": _("Enter a minute between 0 to 60.")})
        return cleaned_data
