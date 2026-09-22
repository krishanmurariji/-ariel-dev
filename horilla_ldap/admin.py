from django.contrib import admin

# Register your models here.

# LDAPSettings deliberately isn't registered here - it holds the LDAP bind
# password, and the default ModelAdmin would show it in plaintext,
# unmasked, to anyone with Django-admin access to it.
