#!/usr/bin/env python3
"""Render assets/og-image.png, the social preview card.

The page used to point og:image at an SVG. X, LinkedIn, Facebook and Slack do
not render SVG previews, so every share showed a blank card. This writes a PNG
at the 1200x630 those platforms expect.

The result is committed rather than built in CI, so the Pages workflow needs no
image dependency and the card is reviewable in a diff like any other asset.

    python3 scripts/make_og_image.py
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from taxagent import __version__  # noqa: E402

WIDTH, HEIGHT = 1200, 630
INK = "#e8e8e5"
MUTED = "#9b9b95"
ACCENT = "#6cc08a"
BACKGROUND = "#17181a"


def render() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)

    # A rule in the accent colour, so the card reads as deliberate at thumbnail
    # size where the smaller type does not survive.
    draw.rectangle([(0, 0), (WIDTH, 10)], fill=ACCENT)

    draw.text((80, 190), "Tax Agent", font=ImageFont.load_default(size=86), fill=INK)
    subtitle = ImageFont.load_default(size=34)
    draw.text((80, 310), "Claude skills for US investment tax analysis",
              font=subtitle, fill=MUTED)
    draw.text((80, 360), "backed by a deterministic engine", font=subtitle, fill=MUTED)
    draw.text((80, 470), f"analysis only · v{__version__} · Apache-2.0",
              font=ImageFont.load_default(size=26), fill=ACCENT)
    return image


def main() -> None:
    out = os.path.join(ROOT, "assets", "og-image.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    render().save(out, "PNG", optimize=True)
    print(f"{out} ({os.path.getsize(out):,} bytes, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
