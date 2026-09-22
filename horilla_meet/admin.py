from django.contrib import admin

from horilla_meet import models

# Register your models here.
# GoogleCredential and GoogleCloudCredential deliberately aren't registered
# here - they hold Google OAuth tokens and client secrets, and the default
# ModelAdmin would show them in plaintext, unmasked, to anyone with
# Django-admin access to them.
admin.site.register(models.GoogleMeeting)
