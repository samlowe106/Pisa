"""Fetch a Wikimedia Commons image + its attribution, sized to a target resolution.

Given a Commons ``File:`` reference (a page URL, a ``File:`` title, or a bare filename), we ask the
MediaWiki API for the original dimensions + attribution metadata, size the image to a desired
resolution (Commons serves only a fixed set of thumbnail widths and rounds a request *up* to the
next one, so we fetch the nearest rendition at/above the target and downscale it locally to the
exact width with Pillow — see ``best_thumb_width`` / ``download_scaled``), and build the
``{title, author, author_url, license, license_url, source_url}`` sidecar that
``thumbnails._thumbnail_preset_attribution`` reads.

Network access is confined to ``fetch_image_info`` / ``pick_download_url`` / ``download`` /
``download_scaled``; everything else is pure and unit-tested. Pillow (already a dependency) does the
resample — otherwise stdlib only.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from PIL import Image

API_URL = "https://commons.wikimedia.org/w/api.php"
# Wikimedia's API etiquette asks for a descriptive User-Agent identifying the tool + a source.
USER_AGENT = (
    "PisaThumbnailFetcher/1.0 "
    "(Pisa course platform; https://github.com/; Wikimedia Commons thumbnail import)"
)
TIMEOUT = 30

# Extensions we fall back to from a MIME type when a download URL carries no usable suffix.
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


# --- Title parsing (pure) ----------------------------------------------------------------


def parse_file_title(url_or_name: str) -> str:
    """Normalise a Commons reference to a canonical ``File:Name.ext`` title.

    Accepts a File: page URL (``/wiki/File:...`` or ``?title=File:...``), a ``File:...`` title, or a
    bare filename. Percent-decodes, turns underscores into spaces, and prepends ``File:`` if absent.
    """
    ref = url_or_name.strip()
    if ref.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(ref)
        query_title = urllib.parse.parse_qs(parsed.query).get("title", [""])[0]
        if query_title:
            ref = query_title
        elif "/wiki/" in parsed.path:
            ref = parsed.path.split("/wiki/", 1)[1]
        else:
            ref = parsed.path.rsplit("/", 1)[-1]
    ref = urllib.parse.unquote(ref).replace("_", " ").strip()
    if not ref:
        raise ValueError(f"Could not extract a File: title from {url_or_name!r}")
    if not re.match(r"(?i)(file|image):", ref):
        ref = f"File:{ref}"
    return ref


def title_from_filename(file_title: str) -> str:
    """Human title from a ``File:`` title: drop the namespace + extension, underscores to spaces."""
    stem = re.sub(r"(?i)^(file|image):", "", file_title).rsplit(".", 1)[0]
    return stem.replace("_", " ").strip()


def extension_for(url: str, mime: str = "") -> str:
    """File extension (with dot) for a download URL — from the URL path, MIME as a fallback.

    Commons thumbnail URLs end in the rendered filename, e.g. ``.../800px-Foo.jpg`` (raster) or
    ``.../800px-Bar.svg.png`` (SVGs rasterize to PNG), so the URL suffix is authoritative.
    """
    last = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    if "." in last:
        return "." + last.rsplit(".", 1)[-1].lower()
    return _MIME_EXTENSIONS.get(mime, ".jpg")


# --- Resolution choice (pure) ------------------------------------------------------------


def best_thumb_width(
    orig_w: int,
    orig_h: int,
    target_w: int,
    target_h: int | None = None,
    metric: str = "l2",
) -> int:
    """Pick the thumbnail width in ``[1, orig_w]`` closest to the desired resolution.

    Because we resample locally after fetching, any width up to the original is achievable, height
    locked to the aspect ratio ``r = orig_h / orig_w`` — so the "resolutions on offer" are the
    family ``{(w, round(w * r)) : 1 <= w <= orig_w}``. We return the ``w`` minimising the distance
    from ``(w, round(w * r))`` to the target, and never upscale.

    * width-only target (``target_h`` is None): distance is monotone in ``|w - target_w|``, so the
      answer is simply ``target_w`` clamped to ``[1, orig_w]`` (``metric`` is irrelevant).
    * 2-D target: minimise L1 or L2 distance to ``(target_w, target_h)`` over the family. We
      evaluate an integer candidate set around the analytic optima (L2 has a closed form; L1's
      optimum sits at a kink ``w = target_w`` or ``w = target_h / r``) plus the endpoints, and take
      the argmin — one code path for both metrics.
    """
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError("original dimensions must be positive")
    r = orig_h / orig_w

    def clamp(value: float) -> int:
        return max(1, min(orig_w, int(round(value))))

    if target_h is None:
        return clamp(target_w)

    l2_optimum = (target_w + r * target_h) / (1 + r * r)
    seeds = {float(target_w), target_h / r, l2_optimum, 1.0, float(orig_w)}
    candidates: set[int] = set()
    for seed in seeds:
        centre = clamp(seed)
        candidates.update({clamp(centre - 1), centre, clamp(centre + 1)})

    def distance(w: int) -> float:
        dw = w - target_w
        dh = round(w * r) - target_h
        return abs(dw) + abs(dh) if metric == "l1" else dw * dw + dh * dh

    # Sort first so ties resolve to the smaller (cheaper) width deterministically.
    return min(sorted(candidates), key=distance)


# --- Attribution parsing (pure) ----------------------------------------------------------


class _ArtistParser(HTMLParser):
    """Pull display text + the first link out of an extmetadata ``Artist`` HTML value."""

    # Elements that delimit text segments. Multilingual author templates repeat the same name
    # in per-language spans with no whitespace between them ("Unknown authorUnknown author"),
    # so parse_artist needs the element boundaries to dedupe the repeats.
    _SEGMENT_TAGS = {"a", "div", "p", "span"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.first_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SEGMENT_TAGS:
            self.text_parts.append("\x00")
        if tag == "a" and self.first_href is None:
            href = dict(attrs).get("href")
            if href:
                self.first_href = href

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


def _absolute_url(href: str) -> str:
    """Resolve a Commons href to an absolute URL (they're often protocol- or site-relative)."""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://commons.wikimedia.org" + href
    return href


def parse_artist(artist_html: str) -> tuple[str, str | None]:
    """``(display_name, author_url|None)`` from an extmetadata ``Artist`` value.

    Plain-text artists (no markup) yield a name and no URL; a linked author yields the first link.
    """
    parser = _ArtistParser()
    parser.feed(artist_html or "")
    segments = [
        re.sub(r"\s+", " ", segment).strip()
        for segment in "".join(parser.text_parts).split("\x00")
    ]
    # Drop empties and adjacent repeats (multilingual templates emit the same name per language).
    kept: list[str] = []
    for segment in segments:
        if segment and (not kept or segment != kept[-1]):
            kept.append(segment)
    name = " ".join(kept)
    href = _absolute_url(parser.first_href) if parser.first_href else None
    return name, href


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def _clean_object_name(value: str) -> str:
    """Strip HTML and the QuickStatements noise Commons embeds in some ``ObjectName`` values.

    Artworks often carry every translation of their title as structured-data markup, e.g.
    ``Death of Archimedes label QS:Len,"Death of Archimedes" label QS:Lde,"Der Tod..."`` —
    everything from the first ``label QS:``/``title QS:`` on is machine data, not the title.
    A leading ``<language>:`` prefix (``Italian: Scuola di Atene``) is dropped the same way.
    """
    text = _strip_html(value)
    parts = re.split(r"\s*\b(?:label|title)\s+QS:", text, maxsplit=1)
    text = parts[0].strip()
    if len(parts) > 1:
        # Only in the multilingual-markup case: drop a "Italian: " style language prefix.
        # (Unconditionally, this would mangle legitimate titles like "Study: a nude".)
        text = re.sub(r"^[A-Z][a-z]+:\s+", "", text)
    return text


def _extmeta(info: ImageInfo, key: str) -> str:
    return (info.extmetadata.get(key) or {}).get("value", "") or ""


def build_attribution(
    info: ImageInfo, *, title_override: str | None = None
) -> dict[str, str]:
    """Build the attribution sidecar dict. Empty fields are omitted (matching ``aurora.json``), and
    keys are sorted so it round-trips cleanly through ``pretty-format-json``."""
    author, author_url = parse_artist(_extmeta(info, "Artist"))
    fields = {
        "title": (
            title_override
            or _clean_object_name(_extmeta(info, "ObjectName"))
            or title_from_filename(info.title)
        ),
        "author": author,
        "author_url": author_url or "",
        "license": _strip_html(_extmeta(info, "LicenseShortName")),
        "license_url": (_extmeta(info, "LicenseUrl") or "").strip(),
        "source_url": info.descriptionurl,
    }
    return {key: value for key, value in sorted(fields.items()) if value}


# --- Network -----------------------------------------------------------------------------


@dataclass
class ImageInfo:
    title: str  # canonical "File:..." title as the API returned it
    url: str  # original file URL
    width: int  # original pixel width
    height: int  # original pixel height
    mime: str
    descriptionurl: str  # the File: page (used as source_url)
    extmetadata: dict  # raw extmetadata block (Artist / LicenseShortName / ...)


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=TIMEOUT
    ) as response:  # noqa: S310 - https only
        return response.read()


def _api_get(params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json"})
    return json.loads(_http_get(f"{API_URL}?{query}"))


def _single_page(data: dict) -> dict:
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), None)
    if page is None or "missing" in page or "imageinfo" not in page:
        title = page.get("title", "?") if page else "?"
        raise LookupError(f"No such Commons file (or no image info): {title}")
    return page


def fetch_image_info(title: str) -> ImageInfo:
    """Query the API for the original dimensions + attribution metadata of ``title``."""
    data = _api_get(
        {
            "action": "query",
            "prop": "imageinfo",
            "titles": title,
            "iiprop": "url|size|mime|extmetadata",
        }
    )
    page = _single_page(data)
    info = page["imageinfo"][0]
    return ImageInfo(
        title=page["title"],
        url=info["url"],
        width=int(info["width"]),
        height=int(info["height"]),
        mime=info.get("mime", ""),
        descriptionurl=info.get("descriptionurl", ""),
        extmetadata=info.get("extmetadata", {}),
    )


def pick_download_url(info: ImageInfo, target_width: int) -> tuple[str, bool]:
    """URL to download for ~``target_width`` px, and whether it's the original file.

    Commons serves only a fixed set of thumbnail widths and rounds a request *up* to the next
    allowed one, so this returns that rendition (>= target_width, usually a bit larger — the caller
    downscales it to the exact target with ``download_scaled``). At or above the original width we
    return the original untouched (never upscale); those go through ``download`` verbatim, which
    also keeps vector SVG originals intact (Pillow can't open SVG).
    """
    if target_width >= info.width:
        return info.url, True
    data = _api_get(
        {
            "action": "query",
            "prop": "imageinfo",
            "titles": info.title,
            "iiprop": "url|size",
            "iiurlwidth": str(target_width),
        }
    )
    return _single_page(data)["imageinfo"][0]["thumburl"], False


def download(url: str, dest: Path) -> None:
    """Write ``url`` to ``dest`` verbatim (no re-encode). Used for original files."""
    dest.write_bytes(_http_get(url))


def download_scaled(url: str, target_width: int, dest: Path) -> tuple[int, int]:
    """Download a raster rendition and write it to ``dest`` at exactly ``target_width`` px wide.

    Commons' served thumbnail is usually a little larger than requested (it rounds up to an allowed
    bucket), so we resample down locally with Pillow (Lanczos). If it already fits within
    ``target_width`` the bytes are written verbatim (no re-encode). Returns the final ``(w, h)``.
    Only raster thumbnails reach here — original/SVG downloads go through ``download``.
    """
    raw = _http_get(url)
    with Image.open(BytesIO(raw)) as image:
        if image.width <= target_width:
            dest.write_bytes(raw)
            return image.width, image.height
        height = round(target_width * image.height / image.width)
        resized = image.resize((target_width, height), Image.Resampling.LANCZOS)
        if dest.suffix.lower() in {".jpg", ".jpeg"}:
            resized.save(dest, quality=90, optimize=True)
        else:
            resized.save(dest)
        return target_width, height
