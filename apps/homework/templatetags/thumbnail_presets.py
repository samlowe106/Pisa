"""Template access to the preset images in ``static/homework/img/thumbnails/``."""

from django import template

from .. import thumbnails

register = template.Library()


@register.simple_tag
def thumbnail_preset(key):
    """URL + attribution sidecar for a preset image:
    ``{% thumbnail_preset "mitosis.jpg" as banner %}`` → ``banner.url`` /
    ``banner.attribution`` (see ``apps.homework.thumbnails.thumbnail_preset``)."""
    return thumbnails.thumbnail_preset(key)
