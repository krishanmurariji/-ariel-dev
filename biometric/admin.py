"""
Register models with the Django admin site.

This section of code registers the BiometricDevices and BiometricEmployees models
with the Django admin site, allowing them to be managed via the admin interface.

"""

from django.contrib import admin

from .models import BiometricEmployees, COSECAttendanceArguments

# Register your models here.
# BiometricDevices deliberately isn't registered here - it holds each
# device's password/API key/secret/token, and the default ModelAdmin
# would show them in plaintext, unmasked, to anyone with Django-admin
# access to it.
admin.site.register(BiometricEmployees)
admin.site.register(COSECAttendanceArguments)
