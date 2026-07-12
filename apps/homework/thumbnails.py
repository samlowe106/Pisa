"""Preset course thumbnails: filesystem discovery + attribution sidecars.

Drop image files into ``static/homework/img/thumbnails/`` and they appear in the course-form
picker automatically (keyed by filename); an optional ``<name>.json`` sidecar next to each image
carries its attribution (see ``static/homework/img/thumbnails/README.md`` and the
``fetch_commons_thumbnail`` management command).

This module must stay free of model imports — ``models.py`` imports it for
``Course.thumbnail_url`` / ``Course.thumbnail_credit``.
"""

import json
from pathlib import Path

from django.conf import settings
from django.templatetags.static import static

# Preset course thumbnails: drop image files into static/<this dir>/ and they appear in the
# course-form picker automatically.
THUMBNAIL_PRESET_DIR = "homework/img/thumbnails"
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".svg", ".webp", ".gif"}


def _thumbnail_preset_attribution(key):
    """Attribution for a preset image, read from its ``<stem>.json`` sidecar (``{}`` if none).

    The sidecar may contain any of: title, author, author_url, license, license_url, source_url.
    """
    stem = Path(key).stem
    for base in settings.STATICFILES_DIRS:
        sidecar = Path(base) / THUMBNAIL_PRESET_DIR / f"{stem}.json"
        if sidecar.is_file():
            try:
                return json.loads(sidecar.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return {}
    return {}


def thumbnail_preset(key):
    """URL + attribution for one preset image by filename, for pages that use a specific
    preset directly (e.g. the login banner). The URL is built whether or not the file has
    been fetched yet; the attribution is ``{}`` until its sidecar exists."""
    return {
        "key": key,
        "url": static(f"{THUMBNAIL_PRESET_DIR}/{key}"),
        "attribution": _thumbnail_preset_attribution(key),
    }


def available_thumbnail_presets():
    """Preset thumbnails the site provides. Drop image files into
    ``static/homework/img/thumbnails/`` and they appear here automatically (keyed by filename);
    an optional ``<name>.json`` sidecar next to each image carries its attribution.
    """
    presets = []
    seen = set()
    for base in settings.STATICFILES_DIRS:
        directory = Path(base) / THUMBNAIL_PRESET_DIR
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in THUMBNAIL_EXTENSIONS and path.name not in seen:
                seen.add(path.name)
                presets.append(
                    {
                        "key": path.name,
                        "label": path.stem.replace("-", " ").replace("_", " ").title(),
                        "url": static(f"{THUMBNAIL_PRESET_DIR}/{path.name}"),
                        "attribution": _thumbnail_preset_attribution(path.name),
                    }
                )
    return presets
