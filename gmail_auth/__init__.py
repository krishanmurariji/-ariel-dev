"""
gmail_auth/__init__.py
"""

from gmail_auth import scheduler as _scheduler

# Add these in horilla/settings.py

"""

installed_apps = [
    ...
    'gmail_auth',
    ...
]

"""
# NOTE: Horilla should be run in https
# Outgoing mail is out of scope for this app - it only reads inbox
# messages for the recruitment candidate email intake feature.

# Please add the redirect URI below in the Google Cloud Console OAuth
# client's "Authorized redirect URIs" and in the GoogleApi model's
# google_redirect_uri field

"""

https://<your_domain.com>/gmail/callback

"""
