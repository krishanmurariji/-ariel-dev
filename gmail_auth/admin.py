"""
gmail_auth/admin.py
"""

from django.contrib import admin

from gmail_auth import models

# Register your models here.


admin.site.register(
    [
        models.GoogleApi,
    ]
)
