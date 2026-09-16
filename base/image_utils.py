"""
base/image_utils.py

Shared helpers for profile-photo-style ImageFields (Employee.employee_profile,
Candidate.profile): a size validator, a "did this field's file actually
change" check used to gate expensive work, and thumbnail generation.

Every avatar shown anywhere in the app - a 32px kanban card image or a
profile page photo - previously loaded the exact original upload, however
large. These are used from each model's save() to also populate a small
paired *_thumbnail field, which get_avatar() then prefers.
"""

import io
import logging

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_PROFILE_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB - matches CompanyForm.validate_image's existing limit
THUMBNAIL_SIZE = (150, 150)
THUMBNAIL_QUALITY = 85


def validate_profile_image_size(file):
    """
    Model-field validator (pass as validators=[validate_profile_image_size]
    on an ImageField) - rejects an upload over MAX_PROFILE_IMAGE_SIZE. Runs
    on every save path via full_clean(), not just form-bound ones (e.g. the
    onboarding portal's own upload view).
    """
    size = getattr(file, "size", None)
    if size and size > MAX_PROFILE_IMAGE_SIZE:
        raise ValidationError(
            _("Image must be under %(limit)s MB.")
            % {"limit": MAX_PROFILE_IMAGE_SIZE // (1024 * 1024)}
        )


def file_field_changed(instance, field_name):
    """
    True if `field_name` on `instance` (unsaved, about to be save()'d) holds
    a different file path than what's currently stored in the DB - a new
    upload, a clear, or a real change - as opposed to an unrelated save that
    just happens to re-touch a FileField/ImageField without changing it.
    Thumbnail regeneration is gated on this so it only runs when there's
    actually new image content to read, not on every save() call.
    """
    current = getattr(instance, field_name)
    current_name = (getattr(current, "name", None) or "").strip()
    if not instance.pk:
        return bool(current_name)
    stored_name = (
        type(instance)
        ._default_manager.filter(pk=instance.pk)
        .values_list(field_name, flat=True)
        .first()
        or ""
    ).strip()
    return current_name != stored_name


def make_thumbnail(file, size=THUMBNAIL_SIZE, quality=THUMBNAIL_QUALITY):
    """
    Returns a ContentFile holding a resized JPEG thumbnail of `file` (a
    Django File/ImageFieldFile), or None if it can't be decoded as a raster
    image - an SVG upload, a corrupt file, anything Pillow can't open. Never
    raises: thumbnail generation is a nice-to-have and must never block the
    underlying profile photo itself from saving, since that field already
    went through its own required-format validation via clean().
    """
    try:
        file.seek(0)
        image = Image.open(file)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info("Thumbnail generation skipped for %s: %s", getattr(file, "name", file), exc)
        return None
    finally:
        try:
            file.seek(0)
        except Exception:
            pass

    # JPEG has no alpha channel - flatten a transparent PNG/palette GIF onto
    # white first, rather than letting save() below error out or silently
    # turn transparency into black.
    if image.mode not in ("RGB", "L"):
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, (255, 255, 255))
        flattened.paste(rgba, mask=rgba.split()[-1])
        image = flattened

    image.thumbnail(size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    buffer.seek(0)

    # ImageField.pre_save() requires a name to save a bare file object at
    # all - upload_to (both models use the shared horilla.models.upload_path)
    # rewrites it to a real storage path anyway, so the base name just needs
    # to exist and look like a JPEG.
    source_name = getattr(file, "name", None) or "thumbnail"
    base_name = source_name.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "thumbnail"
    return ContentFile(buffer.read(), name=f"{base_name}_thumb.jpg")
