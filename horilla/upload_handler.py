"""
horilla/upload_handler.py

One safety check for every file anyone uploads (forms, the API, imports),
run while the file is still arriving - before it is stored or looked at by
any view. Rejecting is done by raising MultiPartParserError, which Django
answers with a plain "400 Bad Request" and a log entry.

What is refused:
  * executable / script / web-page file types (.exe .php .js .html ...), also
    when hidden behind a harmless last extension ("invoice.php.jpg")
  * a file whose real content is not what its extension says (a ".pdf" that
    is not a PDF, a ".png" that is not an image, a ".docx" that is not an
    Office document) - so "malware.exe" renamed to "malware.pdf" is caught
  * any program (Windows .exe/.dll, Linux ELF) or PHP code, whatever the name
  * an SVG picture that contains script (SVG can carry JavaScript)
  * a file bigger than UPLOAD_MAX_BYTES (default 25 MB)

Settings (all optional):
  UPLOAD_MAX_BYTES            largest single file, in bytes
  UPLOAD_BLOCKED_EXTENSIONS   replaces the default blocked list
"""

import logging
import os
import re

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler
from django.http.multipartparser import MultiPartParserError

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 25 * 1024 * 1024
SVG_SCAN_LIMIT = 1024 * 1024

DEFAULT_BLOCKED_EXTENSIONS = frozenset(
    {
        # programs and installers
        ".exe", ".dll", ".msi", ".scr", ".com", ".pif", ".jar", ".war", ".apk",
        ".lnk", ".reg", ".swf",
        # scripts
        ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1",
        ".psm1", ".sh", ".bash", ".py", ".pl", ".rb", ".cgi", ".hta",
        # server-side pages
        ".php", ".php3", ".php4", ".php5", ".php7", ".phtml", ".phar", ".jsp",
        ".jspx", ".asp", ".aspx",
        # web pages (would run script if served back from our own address)
        ".html", ".htm", ".xhtml", ".shtml", ".mht", ".mhtml",
    }
)  # fmt: skip

_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
)
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_OFFICE_ZIP_EXTENSIONS = {".docx", ".xlsx", ".pptx"}

_SVG_SCRIPT = re.compile(
    rb"<\s*script|javascript\s*:|\bon[a-z]+\s*=|<\s*foreignobject|<\s*iframe|"
    rb"<\s*embed|<\s*object|data\s*:\s*text/html",
    re.I,
)
_PHP_CODE = re.compile(rb"<\?php", re.I)


def _blocked_extensions():
    configured = getattr(settings, "UPLOAD_BLOCKED_EXTENSIONS", None)
    return frozenset(configured) if configured is not None else DEFAULT_BLOCKED_EXTENSIONS


def _looks_like_webp(head):
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def content_problem(extension, head):
    """Why ``head`` (the first bytes of a file) cannot be a genuine ``extension`` file, or None."""
    if head[:2] == b"MZ" or head[:4] == b"\x7fELF":
        return "the file is a program, not a document"
    if _PHP_CODE.search(head[:4096]):
        return "the file contains PHP code"
    if len(head) < 8:
        return None  # too short to tell; nothing dangerous fits in it
    if extension == ".pdf" and b"%PDF" not in head[:1024]:
        return "the file is not a real PDF"
    if extension in _IMAGE_EXTENSIONS and not (
        head.startswith(_IMAGE_SIGNATURES) or _looks_like_webp(head)
    ):
        return "the file is not a real image"
    if extension == ".webp" and not _looks_like_webp(head) and not head.startswith(
        _IMAGE_SIGNATURES
    ):
        return "the file is not a real image"
    if extension in _OFFICE_ZIP_EXTENSIONS and not head.startswith(_ZIP_SIGNATURES):
        return "the file is not a real Office document"
    return None


class SafeUploadHandler(FileUploadHandler):
    def _reject(self, reason):
        user = getattr(getattr(self.request, "user", None), "pk", None)
        logger.warning(
            "Upload refused: %s (file %r, user %s, path %s)",
            reason,
            getattr(self, "_name", ""),
            user,
            getattr(self.request, "path", ""),
        )
        raise MultiPartParserError(f"This file was not accepted: {reason}.")

    def new_file(self, field_name, file_name, content_type, content_length, *args, **kwargs):
        super().new_file(field_name, file_name, content_type, content_length, *args, **kwargs)
        self._name = os.path.basename(file_name or "")
        self._received = 0
        self._checked_head = False
        parts = self._name.lower().split(".")[1:]
        self._extension = f".{parts[-1]}" if parts else ""
        self._svg_buffer = b"" if self._extension == ".svg" else None

        blocked = _blocked_extensions()
        if any(f".{part}" in blocked for part in parts):
            self._reject("this type of file is not allowed")
        limit = getattr(settings, "UPLOAD_MAX_BYTES", DEFAULT_MAX_BYTES)
        if content_length and content_length > limit:
            self._reject(f"the file is larger than {limit // (1024 * 1024)} MB")

    def receive_data_chunk(self, raw_data, start):
        self._received += len(raw_data)
        limit = getattr(settings, "UPLOAD_MAX_BYTES", DEFAULT_MAX_BYTES)
        if self._received > limit:
            self._reject(f"the file is larger than {limit // (1024 * 1024)} MB")

        if not self._checked_head:
            self._checked_head = True
            problem = content_problem(self._extension, raw_data[:4096])
            if problem:
                self._reject(problem)

        if self._svg_buffer is not None:
            self._svg_buffer += raw_data
            if len(self._svg_buffer) > SVG_SCAN_LIMIT:
                self._reject("the SVG image is too large")
            if _SVG_SCRIPT.search(self._svg_buffer):
                self._reject("the SVG image contains script")
        return raw_data

    def file_complete(self, file_size):
        return None
