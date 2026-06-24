# Course thumbnail presets

Drop image files (`.jpg`, `.png`, `.svg`, `.webp`, `.gif`) into this directory and they show up
automatically in the course-form thumbnail picker, keyed by filename.

## Attribution (e.g. for Wikimedia Commons images)

To credit an image, add a sidecar JSON file with the **same name** next to it
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
