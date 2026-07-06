"""Download Wikimedia Commons image(s) + an attribution sidecar into the course-thumbnail presets.

    manage.py fetch_commons_thumbnail <File: page URL> [more URLs ...] [--width 1000] [--metric l2]

For each reference it queries the Commons API, picks the rendition closest to the desired
resolution (``apps.homework.commons.best_thumb_width``), downloads it into the presets directory,
and writes the matching ``<name>.json`` sidecar that the course-form thumbnail picker credits from.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from apps.homework import commons
from apps.homework.thumbnails import THUMBNAIL_PRESET_DIR

# Be gentle on the Commons API between images in a batch (a tight loop earns a 429).
_BATCH_DELAY_SECONDS = 1.0


def _default_dir() -> Path:
    """The course-thumbnail presets directory (first STATICFILES_DIRS entry), where the app already
    discovers presets via ``available_thumbnail_presets``."""
    dirs = list(settings.STATICFILES_DIRS)
    if not dirs:
        raise CommandError("No STATICFILES_DIRS configured to hold thumbnails.")
    return Path(dirs[0]) / THUMBNAIL_PRESET_DIR


class Command(BaseCommand):
    help = "Download Wikimedia Commons image(s) + attribution sidecar into the thumbnail presets."

    def add_arguments(self, parser):
        parser.add_argument(
            "urls",
            nargs="+",
            help="Commons File: page URL(s), 'File:...' title(s), or bare filename(s).",
        )
        parser.add_argument(
            "--width",
            type=int,
            default=1000,
            help="Desired width in px (default 1000). The closest available rendition is chosen.",
        )
        parser.add_argument(
            "--height",
            type=int,
            default=None,
            help="Desired height in px for a 2-D target (optional; enables the L1/L2 trade-off).",
        )
        parser.add_argument(
            "--metric",
            choices=["l1", "l2"],
            default="l2",
            help="Distance metric for a 2-D target (default l2). Ignored for a width-only target.",
        )
        parser.add_argument(
            "--name",
            default=None,
            help="Output filename stem (single URL only; default: slugified Commons filename).",
        )
        parser.add_argument(
            "--dir",
            dest="directory",
            default=None,
            help="Output directory (default: the course-thumbnail presets directory).",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite an existing image/sidecar with the same name.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be downloaded + the sidecar, without writing anything.",
        )

    def handle(self, *args, **options):
        urls = options["urls"]
        if options["name"] and len(urls) > 1:
            raise CommandError("--name can only be used with a single URL.")

        out_dir = Path(options["directory"]) if options["directory"] else _default_dir()
        if not options["dry_run"]:
            out_dir.mkdir(parents=True, exist_ok=True)

        failures = 0
        for index, url in enumerate(urls):
            if index and not options["dry_run"]:
                time.sleep(_BATCH_DELAY_SECONDS)
            try:
                self._fetch_one(url, out_dir, options)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one bad page shouldn't abort the batch
                failures += 1
                self.stderr.write(self.style.ERROR(f"✗ {url}: {exc}"))

        if failures:
            raise CommandError(f"{failures} of {len(urls)} reference(s) failed.")

    def _fetch_one(self, url, out_dir: Path, options) -> None:
        title = commons.parse_file_title(url)
        info = commons.fetch_image_info(title)
        target_w = commons.best_thumb_width(
            info.width,
            info.height,
            options["width"],
            options["height"],
            options["metric"],
        )
        download_url, is_original = commons.pick_download_url(info, target_w)

        stem = options["name"] or slugify(commons.title_from_filename(info.title))
        if not stem:
            raise CommandError(
                f"Could not derive a filename for {title!r}; pass --name."
            )
        image_path = out_dir / f"{stem}{commons.extension_for(download_url, info.mime)}"
        sidecar_path = out_dir / f"{stem}.json"

        if (image_path.exists() or sidecar_path.exists()) and not options["overwrite"]:
            self.stdout.write(f"• {stem}: already exists, skipping (use --overwrite)")
            return

        attribution = commons.build_attribution(info)
        sidecar_json = json.dumps(attribution, indent=2, sort_keys=True) + "\n"

        # Final size: originals keep their dimensions; thumbnails are resampled to exactly target_w.
        final_w = info.width if is_original else target_w
        final_h = (
            info.height if is_original else round(target_w * info.height / info.width)
        )
        note = "original" if is_original else "resampled"

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] {title}\n"
                f"    image:   {image_path.name}  {final_w}×{final_h} ({note}"
                f"; source {info.width}×{info.height})\n"
                f"    sidecar: {sidecar_path.name}\n"
                + "\n".join(f"    {line}" for line in sidecar_json.splitlines())
            )
            return

        if is_original:
            commons.download(download_url, image_path)
            actual_w, actual_h = info.width, info.height
        else:
            actual_w, actual_h = commons.download_scaled(
                download_url, target_w, image_path
            )
        sidecar_path.write_text(sidecar_json, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ {image_path.name} {actual_w}×{actual_h} ({note}) — "
                f"{attribution.get('title', stem)}"
                f" by {attribution.get('author', 'unknown')}"
                f" ({attribution.get('license', 'no license')})"
            )
        )
