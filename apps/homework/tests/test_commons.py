"""Tests for the Wikimedia Commons scraper (apps/homework/commons.py + the fetch command).

All pure/offline: the network entry points (fetch_image_info / pick_download_url / download /
download_scaled) are monkeypatched for the command tests; everything else is a pure function.
"""

import json
import tempfile
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from PIL import Image

from apps.homework import commons
from apps.homework.commons import ImageInfo

# The extmetadata block from the real API response for File:Spirale_Ulam_150.jpg — public domain,
# plain-text (unlinked) author, no license URL.
SPIRALE = ImageInfo(
    title="File:Spirale Ulam 150.jpg",
    url="https://upload.wikimedia.org/wikipedia/commons/3/34/Spirale_Ulam_150.jpg",
    width=750,
    height=752,
    mime="image/jpeg",
    descriptionurl="https://commons.wikimedia.org/wiki/File:Spirale_Ulam_150.jpg",
    extmetadata={
        "ObjectName": {"value": "Spirale Ulam 150"},
        "Artist": {"value": "Généré avec la librairie GD de PHP, par Cortexd"},
        "LicenseShortName": {"value": "Public domain"},
    },
)


class ParseFileTitleTests(SimpleTestCase):
    def test_wiki_url(self):
        self.assertEqual(
            commons.parse_file_title(
                "https://commons.wikimedia.org/wiki/File:Spirale_Ulam_150.jpg"
            ),
            "File:Spirale Ulam 150.jpg",
        )

    def test_index_php_title_query(self):
        self.assertEqual(
            commons.parse_file_title(
                "https://commons.wikimedia.org/w/index.php?title=File:Foo_Bar.png"
            ),
            "File:Foo Bar.png",
        )

    def test_percent_encoded(self):
        self.assertEqual(
            commons.parse_file_title(
                "https://commons.wikimedia.org/wiki/File:Caf%C3%A9.jpg"
            ),
            "File:Café.jpg",
        )

    def test_bare_filename_gets_prefix(self):
        self.assertEqual(
            commons.parse_file_title("Some Image.jpg"), "File:Some Image.jpg"
        )

    def test_existing_title_kept(self):
        self.assertEqual(
            commons.parse_file_title("File:Already.jpg"), "File:Already.jpg"
        )

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            commons.parse_file_title("https://commons.wikimedia.org/wiki/")


class FilenameHelpersTests(SimpleTestCase):
    def test_title_from_filename(self):
        self.assertEqual(
            commons.title_from_filename("File:Spirale Ulam 150.jpg"), "Spirale Ulam 150"
        )
        self.assertEqual(commons.title_from_filename("File:Foo_Bar.png"), "Foo Bar")

    def test_extension_from_url(self):
        self.assertEqual(commons.extension_for(".../Spirale_Ulam_150.jpg"), ".jpg")
        # SVGs rasterize to PNG at a width -> the URL suffix is authoritative.
        self.assertEqual(commons.extension_for(".../800px-Bar.svg.png"), ".png")

    def test_extension_falls_back_to_mime(self):
        self.assertEqual(
            commons.extension_for("https://x/thumb", mime="image/webp"), ".webp"
        )


class BestThumbWidthTests(SimpleTestCase):
    def test_width_only_downscales(self):
        self.assertEqual(commons.best_thumb_width(2000, 1000, 800), 800)

    def test_width_only_clamps_to_original_no_upscale(self):
        # The Spirale case: desired 1000 but the original is only 750 wide.
        self.assertEqual(commons.best_thumb_width(750, 752, 1000), 750)

    def test_2d_target_metric_matters(self):
        # orig 2000x1000 (r=0.5), target (1000, 1000). L1's optimum sits at the width-matching
        # kink (w=1000); L2's is the closed form ~(W + rH)/(1 + r^2) = 1200. They genuinely differ.
        l1 = commons.best_thumb_width(2000, 1000, 1000, 1000, "l1")
        l2 = commons.best_thumb_width(2000, 1000, 1000, 1000, "l2")
        self.assertEqual(l1, 1000)
        self.assertAlmostEqual(
            l2, 1200, delta=2
        )  # integer h rounding can shift it by 1
        self.assertGreater(l2, l1)

    def test_2d_target_clamped(self):
        # Desired far exceeds the original -> clamp to original width.
        self.assertEqual(commons.best_thumb_width(600, 400, 4000, 4000, "l2"), 600)

    def test_zero_dimension_raises(self):
        with self.assertRaises(ValueError):
            commons.best_thumb_width(0, 100, 500)


class ParseArtistTests(SimpleTestCase):
    def test_plain_text(self):
        name, url = commons.parse_artist("Généré par Cortexd")
        self.assertEqual(name, "Généré par Cortexd")
        self.assertIsNone(url)

    def test_protocol_relative_link(self):
        name, url = commons.parse_artist(
            '<a href="//commons.wikimedia.org/wiki/User:Foo" title="x">Foo Bar</a>'
        )
        self.assertEqual(name, "Foo Bar")
        self.assertEqual(url, "https://commons.wikimedia.org/wiki/User:Foo")

    def test_site_relative_link_and_entities(self):
        name, url = commons.parse_artist('<a href="/wiki/User:Baz">Fo&amp;o</a>')
        self.assertEqual(name, "Fo&o")
        self.assertEqual(url, "https://commons.wikimedia.org/wiki/User:Baz")

    def test_multilingual_repeats_are_deduped(self):
        # The "unknown author" template repeats the name in per-language spans with no
        # whitespace between them; adjacent identical segments collapse to one.
        name, url = commons.parse_artist(
            '<span lang="en">Unknown author</span><span lang="de">Unknown author</span>'
        )
        self.assertEqual(name, "Unknown author")
        self.assertIsNone(url)

    def test_text_around_a_link_is_kept_in_order(self):
        name, _ = commons.parse_artist('Photo by <a href="/wiki/User:X">X</a> in 1900')
        self.assertEqual(name, "Photo by X in 1900")


class CleanObjectNameTests(SimpleTestCase):
    def test_quickstatements_labels_are_cut(self):
        raw = 'Death of Archimedes label QS:Len,"Death of Archimedes" label QS:Lde,"Der Tod des Archimedes"'
        self.assertEqual(commons._clean_object_name(raw), "Death of Archimedes")

    def test_language_prefix_dropped_only_with_qs_markup(self):
        raw = 'Italian: Scuola di Atene title QS:P1476,it:"Scuola di Atene"'
        self.assertEqual(commons._clean_object_name(raw), "Scuola di Atene")
        # Without QS markup a colon is part of the title, not a language prefix.
        self.assertEqual(commons._clean_object_name("Study: a nude"), "Study: a nude")

    def test_plain_titles_pass_through(self):
        self.assertEqual(
            commons._clean_object_name("Spirale Ulam 150"), "Spirale Ulam 150"
        )


class BuildAttributionTests(SimpleTestCase):
    def test_public_domain_plain_author(self):
        self.assertEqual(
            commons.build_attribution(SPIRALE),
            {
                "author": "Généré avec la librairie GD de PHP, par Cortexd",
                "license": "Public domain",
                "source_url": "https://commons.wikimedia.org/wiki/File:Spirale_Ulam_150.jpg",
                "title": "Spirale Ulam 150",
            },
        )

    def test_linked_author_and_license_url(self):
        info = ImageInfo(
            title="File:Mitosis.jpg",
            url="https://upload/x.jpg",
            width=3000,
            height=2000,
            mime="image/jpeg",
            descriptionurl="https://commons.wikimedia.org/wiki/File:Mitosis.jpg",
            extmetadata={
                "ObjectName": {"value": "Mitosis"},
                "Artist": {"value": '<a href="/wiki/User:Jane">Jane Doe</a>'},
                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                "LicenseUrl": {
                    "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                },
            },
        )
        self.assertEqual(
            commons.build_attribution(info),
            {
                "author": "Jane Doe",
                "author_url": "https://commons.wikimedia.org/wiki/User:Jane",
                "license": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "source_url": "https://commons.wikimedia.org/wiki/File:Mitosis.jpg",
                "title": "Mitosis",
            },
        )

    def test_title_falls_back_to_filename(self):
        info = ImageInfo(
            title="File:No Object Name.png",
            url="u",
            width=10,
            height=10,
            mime="image/png",
            descriptionurl="d",
            extmetadata={},  # no ObjectName / Artist / License
        )
        result = commons.build_attribution(info)
        self.assertEqual(result["title"], "No Object Name")
        self.assertNotIn("author", result)  # empty fields omitted


_BIG = ImageInfo(
    title="File:Big.jpg",
    url="https://upload.wikimedia.org/wikipedia/commons/9/99/Big.jpg",
    width=3000,
    height=2000,
    mime="image/jpeg",
    descriptionurl="https://commons.wikimedia.org/wiki/File:Big.jpg",
    extmetadata={
        "Artist": {"value": "Someone"},
        "LicenseShortName": {"value": "CC BY 4.0"},
    },
)


class DownloadScaledTests(SimpleTestCase):
    """The local Pillow downscale (raster thumbnails only)."""

    def _png(self, width: int, height: int) -> bytes:
        buffer = BytesIO()
        Image.new("RGB", (width, height), "white").save(buffer, "PNG")
        return buffer.getvalue()

    def test_resamples_down_to_exact_target_width(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(commons, "_http_get", return_value=self._png(1280, 800)),
        ):
            dest = Path(tmp) / "x.png"
            self.assertEqual(
                commons.download_scaled("http://x", 1000, dest), (1000, 625)
            )
            self.assertEqual(Image.open(dest).size, (1000, 625))

    def test_keeps_bytes_verbatim_when_already_small_enough(self):
        raw = self._png(800, 600)
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(commons, "_http_get", return_value=raw),
        ):
            dest = Path(tmp) / "x.png"
            self.assertEqual(
                commons.download_scaled("http://x", 1000, dest), (800, 600)
            )
            self.assertEqual(dest.read_bytes(), raw)  # verbatim, no re-encode


class FetchCommandTests(SimpleTestCase):
    def _run(self, *args):
        out = StringIO()
        call_command("fetch_commons_thumbnail", *args, stdout=out, stderr=out)
        return out.getvalue()

    def _patched(self):
        return mock.patch.multiple(
            commons,
            fetch_image_info=mock.DEFAULT,
            pick_download_url=mock.DEFAULT,
            download=mock.DEFAULT,
            download_scaled=mock.DEFAULT,
        )

    def test_original_path_downloads_verbatim(self):
        # Spirale is 750px wide; a 1000px target clamps to the original -> download() path.
        with tempfile.TemporaryDirectory() as tmp, self._patched() as mocks:
            mocks["fetch_image_info"].return_value = SPIRALE
            mocks["pick_download_url"].return_value = (SPIRALE.url, True)
            mocks["download"].side_effect = lambda url, dest: Path(dest).write_bytes(
                b"img"
            )

            self._run(
                "https://commons.wikimedia.org/wiki/File:Spirale_Ulam_150.jpg",
                "--dir",
                tmp,
                "--name",
                "spirale-ulam-150",
            )

            self.assertEqual((Path(tmp) / "spirale-ulam-150.jpg").read_bytes(), b"img")
            self.assertEqual(
                json.loads((Path(tmp) / "spirale-ulam-150.json").read_text()),
                commons.build_attribution(SPIRALE),
            )
            mocks["download_scaled"].assert_not_called()
            # The preset IS the original here — no duplicate copy in originals/.
            self.assertFalse((Path(tmp) / "originals").exists())

    def test_downscale_path_uses_download_scaled_with_target_width(self):
        with tempfile.TemporaryDirectory() as tmp, self._patched() as mocks:
            mocks["fetch_image_info"].return_value = _BIG
            mocks["pick_download_url"].return_value = (
                "https://upload.wikimedia.org/.../1280px-Big.jpg",
                False,
            )
            mocks["download_scaled"].side_effect = lambda url, w, dest: (
                Path(dest).write_bytes(b"scaled"),
                (w, 667),
            )[1]
            mocks["download"].side_effect = lambda url, dest: Path(dest).write_bytes(
                b"full-res"
            )

            self._run("File:Big.jpg", "--dir", tmp, "--name", "big", "--width", "1000")

            self.assertEqual((Path(tmp) / "big.jpg").read_bytes(), b"scaled")
            self.assertEqual(
                mocks["download_scaled"].call_args.args[1], 1000
            )  # target width
            # A resampled fetch also keeps the untouched original, out of the picker's
            # (top-level-only) scan.
            self.assertEqual(
                (Path(tmp) / "originals" / "big.jpg").read_bytes(), b"full-res"
            )
            self.assertEqual(mocks["download"].call_args.args[0], _BIG.url)

    def test_skip_original_downloads_only_the_resample(self):
        with tempfile.TemporaryDirectory() as tmp, self._patched() as mocks:
            mocks["fetch_image_info"].return_value = _BIG
            mocks["pick_download_url"].return_value = (
                "https://upload.wikimedia.org/.../1280px-Big.jpg",
                False,
            )
            mocks["download_scaled"].side_effect = lambda url, w, dest: (
                Path(dest).write_bytes(b"scaled"),
                (w, 667),
            )[1]

            self._run("File:Big.jpg", "--dir", tmp, "--name", "big", "--skip-original")

            mocks["download"].assert_not_called()
            self.assertFalse((Path(tmp) / "originals").exists())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, self._patched() as mocks:
            mocks["fetch_image_info"].return_value = SPIRALE
            mocks["pick_download_url"].return_value = (SPIRALE.url, True)

            output = self._run("File:Spirale_Ulam_150.jpg", "--dir", tmp, "--dry-run")

            self.assertEqual(list(Path(tmp).iterdir()), [])  # nothing written
            self.assertIn("dry-run", output)
            mocks["download"].assert_not_called()
            mocks["download_scaled"].assert_not_called()

    def test_dry_run_announces_the_kept_original(self):
        with tempfile.TemporaryDirectory() as tmp, self._patched() as mocks:
            mocks["fetch_image_info"].return_value = _BIG
            mocks["pick_download_url"].return_value = (
                "https://upload.wikimedia.org/.../1280px-Big.jpg",
                False,
            )

            output = self._run("File:Big.jpg", "--dir", tmp, "--dry-run")

            self.assertIn("originals/big.jpg", output)
            self.assertIn("3000×2000", output)
            self.assertEqual(list(Path(tmp).iterdir()), [])  # still nothing written
            mocks["download"].assert_not_called()

    def test_name_rejected_for_multiple_urls(self):
        with self.assertRaises(CommandError):
            self._run("File:A.jpg", "File:B.jpg", "--name", "x")

    def test_no_urls_and_no_manifest_is_an_error(self):
        with self.assertRaises(CommandError):
            self._run("--dry-run")

    def test_from_file_reads_manifest_and_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp, self._patched() as mocks:
            manifest = Path(tmp) / "sources.txt"
            manifest.write_text(
                "# comment line\n"
                "\n"
                "File:Spirale_Ulam_150.jpg  # trailing comment\n"
                "File:Big.jpg\n"
            )
            # Spirale already downloaded -> skipped without any network call.
            (Path(tmp) / "spirale-ulam-150.jpg").write_bytes(b"already here")
            (Path(tmp) / "spirale-ulam-150.json").write_text("{}")

            mocks["fetch_image_info"].side_effect = lambda title: (
                SPIRALE if "Spirale" in title else _BIG
            )
            mocks["pick_download_url"].return_value = (_BIG.url, True)
            mocks["download"].side_effect = lambda url, dest: Path(dest).write_bytes(
                b"img"
            )

            output = self._run("--from-file", str(manifest), "--dir", tmp)

            self.assertIn("skipping", output)  # spirale untouched
            self.assertEqual(
                (Path(tmp) / "spirale-ulam-150.jpg").read_bytes(), b"already here"
            )
            self.assertTrue((Path(tmp) / "big.jpg").exists())
            mocks["download"].assert_called_once()  # only Big was downloaded

    def test_from_file_missing_manifest_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                self._run("--from-file", str(Path(tmp) / "nope.txt"), "--dir", tmp)
