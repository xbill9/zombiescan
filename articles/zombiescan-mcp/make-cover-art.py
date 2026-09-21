#!/usr/bin/env python3
"""Regenerate this article's cover illustration.

    GEMINI_API_KEY=$(cat ~/gemini.key) \
      uv run --with google-genai --with pillow python articles/zombiescan-mcp/make-cover-art.py

Generates the artwork at 16:9 -- the widest ratio the model offers -- and
crops the centre band to 1376x578, which is the 2.381:1 dev.to displays. The
prompt keeps the subjects inside that band, so the crop takes only background.

The output is named by a hash of its bytes: dev.to proxies a cover rather than
re-hosting it, so a regenerated image needs a URL nothing has cached.
"""

from __future__ import annotations

import base64
import hashlib
import os
import pathlib

HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw-cover-16x9.png"
WIDTH, HEIGHT = 1376, 578
MODEL = "gemini-3.1-flash-lite-image"

PROMPT = """A wide cinematic flat-vector illustration for a technical article cover. \
Dark charcoal background, near-black, with a subtle vignette. The composition sits in the \
middle horizontal band, with generous empty dark space along the top and bottom edges.

On the left, a friendly cartoon penguin mascot in the style of Tux the Linux penguin: rounded \
black and white body, bright orange beak and feet, standing confidently, holding a clipboard in \
one flipper and a small flashlight in the other, cool blue rim light along his edges.

On the right, a shambling crowd of cute cartoon zombie server racks: anthropomorphic data-centre \
servers with dented chassis, loose ethernet cables hanging like limp arms, dim green glowing \
status LEDs for eyes, cobwebs and dust, tilting toward the penguin.

Floating above and between them, one large softly glowing orange cloud icon with faint circuit \
traces inside it, casting warm orange light down onto the scene.

Clean geometric shapes, soft gradients, high contrast against the dark background, a teal and \
orange palette. No text, no lettering, no words, no numbers, no brand logos anywhere in the \
image."""


def generate() -> None:
    from google import genai

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("set GEMINI_API_KEY (the nb2lite plugin keeps one in ~/gemini.key)")

    interaction = genai.Client(api_key=key).interactions.create(
        model=MODEL,
        input=PROMPT,
        response_format={"type": "image", "aspect_ratio": "16:9"},
        generation_config={"thinking_level": "high"},
        store=True,
    )
    data = interaction.output_image.data
    RAW.write_bytes(base64.b64decode(data) if isinstance(data, str) else data)
    print(f"wrote {RAW.name}")


def crop() -> None:
    from PIL import Image

    source = Image.open(RAW)
    width, height = source.size
    band = int(round(width / (WIDTH / HEIGHT)))
    top = (height - band) // 2
    cover = source.crop((0, top, width, top + band)).resize((WIDTH, HEIGHT), Image.LANCZOS)

    out = HERE / "devto-cover.jpg"
    cover.convert("RGB").save(out, quality=88, optimize=True)
    final = out.with_name(f"devto-cover.{hashlib.sha256(out.read_bytes()).hexdigest()[:8]}.jpg")
    if final.exists():
        final.unlink()
    out.rename(final)
    print(f"wrote {final.name}  {WIDTH}x{HEIGHT}  {final.stat().st_size // 1024} KB")
    print(
        "cover_image: https://raw.githubusercontent.com/xbill9/zombiescan/main/"
        f"articles/zombiescan-mcp/{final.name}"
    )


if __name__ == "__main__":
    if not RAW.exists():
        generate()
    crop()
