# Course thumbnail presets

Drop image files (`.jpg`, `.png`, `.svg`, `.webp`, `.gif`) into this directory and they show up
automatically in the course-form thumbnail picker, keyed by filename.

## From Wikimedia Commons (automated)

The Commons-fetched presets are **not in version control** (they and their `originals/` are
gitignored) — [`sources.txt`](sources.txt) is the list of record. After a fresh checkout:

```sh
manage.py fetch_commons_thumbnail --from-file
```

fetches everything in the manifest, skipping presets already on disk. To add an image, append
its Commons page URL to `sources.txt` and re-run. One-off fetches work too:

```sh
manage.py fetch_commons_thumbnail "https://commons.wikimedia.org/wiki/File:Mitosis.jpg"
# several at once, a custom size, or a preview:
manage.py fetch_commons_thumbnail <url> <url> ... --width 1000
manage.py fetch_commons_thumbnail <url> --width 1600 --height 360 --metric l1 --dry-run
```

It downloads the image sized to `--width` (default 1000 px; Commons only serves a fixed set of
thumbnail widths, so it fetches the nearest one and downscales locally to the exact size) and
writes the attribution sidecar below from the file's Commons metadata. When the image gets
resampled, the untouched full-resolution original is also kept in `originals/` (pass
`--skip-original` to save the bandwidth/disk; the picker ignores that subdirectory). See
`apps/homework/management/commands/fetch_commons_thumbnail.py`.

## Attribution (manual)

To credit an image by hand, add a sidecar JSON file with the **same name** next to it
(`mitosis.jpg` → `mitosis.json`). Every field is optional:

```json
{
  "title": "Mitosis",
  "author": "Jane Doe",
  "author_url": "https://commons.wikimedia.org/wiki/User:JaneDoe",
  "license": "CC BY-SA 4.0",
  "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
  "source_url": "https://commons.wikimedia.org/wiki/File:Mitosis.jpg"
}
```

When a course uses that preset, the credit renders automatically under the course description —
e.g. *Thumbnail: **Mitosis** by **Jane Doe** (CC BY-SA 4.0)*, with `title`→`source_url`,
`author`→`author_url`, and `license`→`license_url` linked when present. Nothing to type into the
description by hand, and the credit stays attached to the image.
