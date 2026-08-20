"""
gmail_auth/forms.py
"""

from base.forms import ModelForm
from gmail_auth import models


class GmailServerForm(ModelForm):
    """
    GmailServerForm
    """

    class Meta:
        """
        Meta
        """

        model = models.GoogleApi
        fields = "__all__"
