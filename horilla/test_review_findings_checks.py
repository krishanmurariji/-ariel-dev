"""
Checks for three review findings: the upload sniffing when the first piece of a file is tiny, what an
encrypted field returns when it cannot decrypt, and whether the face-detection setup endpoint can be
driven by a session cookie (which is what would make dropping CSRF dangerous).
"""

import io

from django.core.exceptions import ImproperlyConfigured  # noqa: F401
from django.core.files.uploadhandler import StopUpload  # noqa: F401
from django.http import QueryDict
from django.http.multipartparser import MultiPartParser, MultiPartParserError
from django.test import Client, RequestFactory, SimpleTestCase, override_settings

from horilla.crypto_fields import EncryptedJSONField, EncryptedTextField
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from horilla.upload_handler import SafeUploadHandler

BOUNDARY = "BOUNDARY123"
PHP = b"<?php system($_GET['c']); ?>" + b"x" * 200


def multipart_body(filename, content):
    return (
        (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="f"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )


class ShortReads(io.RawIOBase):
    """A request body whose read() hands back at most ``step`` bytes, as a socket may."""

    def __init__(self, data, step):
        self.buffer = io.BytesIO(data)
        self.step = step

    def read(self, size=-1):
        return self.buffer.read(min(size, self.step) if size and size > 0 else self.step)


def parse(filename, content, step=None):
    body = multipart_body(filename, content)
    request = RequestFactory().post("/upload/")
    meta = {
        "CONTENT_TYPE": f"multipart/form-data; boundary={BOUNDARY}",
        "CONTENT_LENGTH": str(len(body)),
    }
    stream = ShortReads(body, step) if step else io.BytesIO(body)
    parser = MultiPartParser(meta, stream, [SafeUploadHandler(request)], "utf-8")
    return parser.parse()


class UploadSniffing(SimpleTestCase):
    def test_php_in_a_pdf_is_refused_when_the_body_arrives_whole(self):
        with self.assertRaises(MultiPartParserError):
            parse("cv.pdf", PHP)

    def test_php_in_a_pdf_is_refused_when_the_body_arrives_in_tiny_reads(self):
        with self.assertRaises(MultiPartParserError):
            parse("cv.pdf", PHP, step=1)

    def test_a_fake_png_is_refused_when_the_body_arrives_in_tiny_reads(self):
        with self.assertRaises(MultiPartParserError):
            parse("photo.png", b"this is not an image" + b"x" * 200, step=1)


class UndecryptableValues(SimpleTestCase):
    def test_a_value_that_cannot_be_decrypted_is_not_handed_back_as_the_secret(self):
        field = EncryptedTextField()
        with override_settings(SECRET_KEY="key-one-" + "a" * 40, FIELD_ENCRYPTION_KEYS=None):
            stored = field.get_prep_value("bind-password")
        with override_settings(SECRET_KEY="key-two-" + "b" * 40, FIELD_ENCRYPTION_KEYS=None):
            try:
                value = field.from_db_value(stored, None, None)
            except Exception:
                return  # failing loudly is the safe outcome
        self.assertNotEqual(value, stored, "the raw ciphertext came back as if it were the secret")

    def test_an_undecryptable_json_value_reads_as_empty_not_as_ciphertext(self):
        field = EncryptedJSONField()
        with override_settings(SECRET_KEY="key-one-" + "a" * 40, FIELD_ENCRYPTION_KEYS=None):
            stored = field.get_prep_value({"client_secret": "s3cret"})
        with override_settings(SECRET_KEY="key-two-" + "b" * 40, FIELD_ENCRYPTION_KEYS=None):
            self.assertEqual(field.from_db_value(stored, None, None), {})

    def test_an_old_plaintext_row_still_reads_as_is(self):
        field = EncryptedTextField()
        self.assertEqual(field.from_db_value("plain-old-password", None, None), "plain-old-password")


class FaceDetectionSetup(IsolatedTestCase):
    def test_a_session_cookie_alone_does_not_authenticate_the_endpoint(self):
        user, _employee = make_employee("fd_user")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post("/api/facedetection/setup/", {"image": "x"})
        self.assertIn(response.status_code, (401, 403))


SVG_NS = b'xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg"'


class SvgFilter(SimpleTestCase):
    """Payloads that run script in an SVG viewed on its own, written to slip past a text search."""

    def accepted(self, body):
        try:
            parse("logo.svg", body)
        except MultiPartParserError:
            return False
        return True

    def test_the_plain_script_tag_is_refused(self):
        self.assertFalse(self.accepted(b"<svg %s><script>alert(1)</script></svg>" % SVG_NS))

    def test_a_script_element_written_with_a_namespace_prefix_is_refused(self):
        self.assertFalse(self.accepted(b"<svg %s><svg:script>alert(1)</svg:script></svg>" % SVG_NS))

    def test_a_javascript_link_with_an_encoded_letter_is_refused(self):
        body = b'<svg %s xmlns:xlink="http://www.w3.org/1999/xlink"><a xlink:href="&#106;avascript:alert(1)"><text>x</text></a></svg>'
        self.assertFalse(self.accepted(body % SVG_NS))


class SvgHeaders(SimpleTestCase):
    def headers_for(self, path):
        from django.http import HttpResponse

        from horilla.horilla_middlewares import SVGSecurityMiddleware

        middleware = SVGSecurityMiddleware(lambda request: HttpResponse("<svg/>"))
        return middleware(RequestFactory().get(path))

    def test_lowercase_svg_path_gets_the_csp(self):
        self.assertIn("Content-Security-Policy", self.headers_for("/media/a/b/logo-1234abcd.svg"))

    def test_uppercase_svg_extension_gets_the_csp_too(self):
        self.assertIn("Content-Security-Policy", self.headers_for("/media/a/b/logo-1234abcd.SVG"))
