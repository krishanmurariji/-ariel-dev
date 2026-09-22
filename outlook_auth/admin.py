"""
outlook_auth/admin.py
"""

from django.contrib import admin

from outlook_auth import models

# Register your models here.

# AzureApi deliberately isn't registered here - it holds the Azure client
# secret and the Outlook OAuth tokens, and the default ModelAdmin would show
# both in plaintext, unmasked, to anyone with Django-admin access to it. The
# app's own permission-gated "Mail Servers" settings page is the only UI for it.
admin.site.register(models.OutlookMeeting)
