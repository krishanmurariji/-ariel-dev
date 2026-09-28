"""
horilla/backends.py

DISABLED - the ZimbraBackend SMTP backend below is commented out.

Nothing uses it: EMAIL_BACKEND is "base.backends.ConfiguredEmailBackend"
(horilla/settings/base.py) - confirmed as the backend actually in effect on
production - and no code, template, setting or fixture in the repository
refers to this module, ZimbraBackend or CustomSSLContext.

It was also unsafe to switch on. CustomSSLContext called
ssl.SSLContext.__init__() with no arguments and no certificate store, so
the STARTTLS connection it built did not verify the mail server's
certificate or hostname - anyone able to sit between this server and the
mail server could read the SMTP login and the mail. It also print()ed status
messages and swallowed every connection error.

The original code is kept below, commented out, rather than deleted. If a
Zimbra backend is ever needed again, rebuild it on
ssl.create_default_context() (which verifies the chain and hostname) and log
through the logging module instead of print().
"""

# ----------------------------------------------------------------------
# Original implementation - disabled, see the module docstring.
# ----------------------------------------------------------------------
# import ssl
#
# from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
#
# # import certifi
# from base.models import EmailLog
#
#
# class CustomSSLContext(ssl.SSLContext):
#     """
#     CustomSSLContext
#     """
#
#     def __init__(self):
#         super().__init__()
#         # self.load_verify_locations(cafile=certifi.where())
#
#
# class ZimbraBackend(SMTPBackend):
#     """
#     ZimbraBackend Class
#     """
#
#     def __init__(self, *args, **kwargs):
#         # Initialize the parent SMTP backend
#         super().__init__(*args, **kwargs)
#         self.ssl_context = CustomSSLContext()
#
#     def open(self):
#         """Establish the SMTP connection using the custom SSL context and TLS."""
#         if self.connection:
#             return False  # Connection already open
#
#         try:
#             # Create the SMTP connection with the custom SSL context
#             self.connection = self.connection_class(
#                 host=self.host, port=self.port, timeout=self.timeout
#             )
#
#             if self.use_tls:
#                 # Start TLS with custom SSL context
#                 self.connection.starttls(context=self.ssl_context)
#                 print(f"Started TLS on {self.host}:{self.port}")
#
#             # Authenticate after starting TLS
#             self.connection.login(self.username, self.password)
#             print(
#                 f"Successfully connected and authenticated on {self.host}:{self.port}"
#             )
#
#         except Exception as e:
#             print(f"Failed to connect or authenticate: {e}")
#             self.close()
#             return False
#         return True
#
#     def send_messages(self, email_messages):
#         """Send messages and log the results to the database."""
#         if not self.open():
#             print("Failed to open connection.")
#             return 0
#
#         response = super().send_messages(email_messages)
#         for message in email_messages:
#             # Log each email to the EmailLog model
#             email_log = EmailLog(
#                 subject=message.subject,
#                 from_email=message.from_email,
#                 to=message.to,
#                 body=message.body,
#                 status="sent" if response else "failed",
#             )
#             email_log.save()
#         return response
#
#     def close(self):
#         """Ensure the SMTP connection is closed after use."""
#         if self.connection:
#             try:
#                 self.connection.quit()
#             except Exception as e:
#                 print(f"Error closing connection: {e}")
#             finally:
#                 self.connection = None
