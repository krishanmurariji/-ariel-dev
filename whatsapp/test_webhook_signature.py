"""
The WhatsApp webhook used to accept any POST, so anyone who found its URL could
inject fake "incoming messages". Meta signs each delivery (X-Hub-Signature-256,
an HMAC-SHA256 of the raw body keyed with the app's App Secret); verification
switches on once an App Secret is saved and, until then, deliveries are still
accepted so nothing breaks before the secret is entered.
"""

import hashlib
import hmac
import json

from django.test import Client, RequestFactory
from django.urls import reverse

from base.models import IntegrationApps
from horilla.isolated_testcase import IsolatedTestCase
from whatsapp.models import WhatsappCredientials
from whatsapp.security import verify_meta_signature

URL = reverse("whatsapp")
SECRET = "meta-app-secret-for-tests"
BODY = json.dumps({"object": "something_else", "entry": []}).encode()


def sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_credentials(app_secret=""):
    return WhatsappCredientials.objects.create(
        meta_token="token",
        meta_business_id="1",
        meta_phone_number_id="2",
        meta_phone_number="3",
        meta_webhook_token="verify-me",
        meta_app_secret=app_secret,
        is_primary=True,
    )


def post(body=BODY, signature=None):
    extra = {"HTTP_X_HUB_SIGNATURE_256": signature} if signature is not None else {}
    return RequestFactory().post(
        URL, data=body, content_type="application/json", **extra
    )


class VerifySignatureTests(IsolatedTestCase):
    def test_nothing_to_verify_without_an_app_secret(self):
        make_credentials(app_secret="")
        self.assertIsNone(verify_meta_signature(post()))

    def test_valid_signature_passes(self):
        make_credentials(SECRET)
        self.assertTrue(verify_meta_signature(post(signature=sign(BODY))))

    def test_missing_wrong_or_malformed_signature_fails(self):
        make_credentials(SECRET)
        self.assertFalse(verify_meta_signature(post()))
        self.assertFalse(verify_meta_signature(post(signature=sign(BODY, "wrong"))))
        self.assertFalse(verify_meta_signature(post(signature="not-a-signature")))
        self.assertFalse(verify_meta_signature(post(signature=sign(b"other body"))))

    def test_any_configured_secret_is_accepted(self):
        make_credentials("first-secret")
        second = make_credentials("second-secret")
        second.is_primary = False
        second.save()
        self.assertTrue(
            verify_meta_signature(post(signature=sign(BODY, "second-secret")))
        )


class WebhookViewTests(IsolatedTestCase):
    def setUp(self):
        IntegrationApps.objects.create(app_label="whatsapp", is_enabled=True)
        self.client = Client()

    def deliver(self, body=BODY, signature=None):
        extra = (
            {"HTTP_X_HUB_SIGNATURE_256": signature} if signature is not None else {}
        )
        return self.client.post(
            URL, data=body, content_type="application/json", **extra
        )

    def test_forged_post_is_rejected_once_a_secret_is_configured(self):
        make_credentials(SECRET)
        self.assertEqual(self.deliver().status_code, 403)
        self.assertEqual(self.deliver(signature=sign(BODY, "wrong")).status_code, 403)

    def test_correctly_signed_post_is_processed(self):
        make_credentials(SECRET)
        self.assertNotEqual(self.deliver(signature=sign(BODY)).status_code, 403)

    def test_unsigned_post_still_accepted_until_a_secret_is_saved(self):
        make_credentials("")
        self.assertNotEqual(self.deliver().status_code, 403)

    def test_malformed_json_is_a_400_not_a_crash(self):
        make_credentials(SECRET)
        body = b"{not json"
        self.assertEqual(self.deliver(body, signature=sign(body)).status_code, 400)
