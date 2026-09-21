#!/usr/bin/env python3
"""Regenerate this article's AWS Builder Center cover illustration.

    GEMINI_API_KEY=$(cat ~/gemini.key) \
      uv run --with google-genai --with pillow python articles/zombiescan/make-cover-art.py

Builder Center asks for 1200x675 and says text in images is not recommended,
which the dev.to cover for this article is made of. So the two destinations get
different art rather than one crop: this one carries no lettering at all.

The model draws at 16:9, the widest ratio it offers, and the centre band is cut
to 1200x675 -- a 1.8% trim, because 16:9 is 1.778 and the model returns 1.792.
The prompt asks for a full-bleed scene: asked instead for "empty space at the
top and bottom" it returns a letterboxed strip with hard black bars, which no
crop can recover.

Output is named by a hash of its bytes, the same rule the dev.to covers follow.
"""

from __future__ import annotations

import base64
import hashlib
import os
import pathlib

HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw-cover-16x9.png"
WIDTH, HEIGHT = 1200, 675
MODEL = "gemini-3.1-flash-lite-image"

PROMPT = """A wide cinematic flat-vector illustration for a technical article cover, \
filling the entire frame edge to edge with no letterbox bars, no black borders and no \
margins -- the scene reaches the top, bottom, left and right edges of the image.

A dim data-centre aisle at night, seen straight on. Server racks run away to both sides \
and their tops reach the top edge of the frame; the tiled floor reaches the bottom edge.

A friendly cartoon penguin mascot in the style of Tux the Linux penguin -- rounded black \
and white body, bright orange beak and feet -- stands at the left sweeping a warm torch \
beam across the aisle, cool blue rim light along his edges.

The beam picks out forgotten cloud infrastructure left lying about: a disconnected disk \
platter tilted against a rack with its cable coiled loose, a small gateway box with \
cobwebs across its ports, a dusty unplugged network switch. Each abandoned object has a \
small blank paper price tag hanging from it by a thread, catching the torchlight.

A large softly glowing orange cloud icon floats high in the aisle with faint circuit \
traces inside it, casting warm light down over the scene.

Dark charcoal palette with teal and orange, clean geometric shapes, soft gradients, dust \
motes in the beam. Absolutely no text, no lettering, no words, no numbers, no digits, no \
brand logos anywhere -- the price tags are blank."""


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

    out = HERE / "builder-cover.jpg"
    cover.convert("RGB").save(out, quality=88, optimize=True)
    final = out.with_name(f"builder-cover.{hashlib.sha256(out.read_bytes()).hexdigest()[:8]}.jpg")
    if final.exists():
        final.unlink()
    out.rename(final)
    print(f"wrote {final.name}  {WIDTH}x{HEIGHT}  {final.stat().st_size // 1024} KB")


if __name__ == "__main__":
    if not RAW.exists():
        generate()
    crop()
