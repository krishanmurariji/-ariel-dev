from django.contrib import admin

from whatsapp.models import WhatsappFlowDetails

# Register your models here.
# WhatsappCredientials deliberately isn't registered here - it holds the
# Meta access token and webhook verify token, and the default ModelAdmin
# would show both in plaintext, unmasked, to anyone with Django-admin
# access to it.
admin.site.register(WhatsappFlowDetails)
