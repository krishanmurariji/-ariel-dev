"""
gmail_auth/admin.py
"""

# GoogleApi deliberately isn't registered here - it holds the Google client
# secret and OAuth token, and the default ModelAdmin would show both in
# plaintext, unmasked, to anyone with Django-admin access to it. The app's
# own permission-gated "Mail Servers" settings page is the only UI for it.
