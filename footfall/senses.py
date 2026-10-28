"""What a persona's eyes and hands do to the session.

The model never sees the real screen: each screenshot goes through the persona's eyesight
first (blur, contrast loss, yellowed lens, colour vision deficiency), and each click goes through
their hands (tremor). The persona's limits are physical, not a line in a prompt.
"""

import io
import random

from PIL import Image, ImageEnhance, ImageFilter

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB.
CVD = {
    "deuteranopia": (0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413,
                     -0.011820, 0.042940, 0.968881),
    "protanopia": (0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216,
                   -0.003882, -0.048116, 1.051998),
    "tritanopia": (1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602,
                   0.004733, 0.691367, 0.303900),
}


def _srgb_to_linear(v):
    c = v / 255
    return round(255 * (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4))


def _linear_to_srgb(v):
    c = v / 255
    return round(255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055))


TO_LINEAR = [_srgb_to_linear(v) for v in range(256)] * 3
TO_SRGB = [_linear_to_srgb(v) for v in range(256)] * 3


def colour_vision(img, kind):
    m = CVD[kind]
    linear = img.point(TO_LINEAR)
    mixed = linear.convert("RGB", (*m[0:3], 0, *m[3:6], 0, *m[6:9], 0))
    return mixed.point(TO_SRGB)


def see(img, eyes):
    """Degrade a screenshot the way this persona sees it. `eyes` keys, all optional:
    blur (px), contrast (1 = normal), brightness, tint (r, g, b multipliers), colour (CVD kind)."""
    if not eyes:
        return img
    if eyes.get("colour"):
        img = colour_vision(img, eyes["colour"])
    if eyes.get("tint"):
        r, g, b = eyes["tint"]
        img = img.convert("RGB", (r, 0, 0, 0, 0, g, 0, 0, 0, 0, b, 0))
    if eyes.get("contrast", 1) != 1:
        img = ImageEnhance.Contrast(img).enhance(eyes["contrast"])
    if eyes.get("brightness", 1) != 1:
        img = ImageEnhance.Brightness(img).enhance(eyes["brightness"])
    if eyes.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(eyes["blur"]))
    return img


def see_png(png, eyes, zoom=1.0):
    """The PNG the model gets: degraded in screen pixels, then enlarged if `zoom` > 1."""
    if not eyes and zoom == 1:
        return png
    img = see(Image.open(io.BytesIO(png)).convert("RGB"), eyes)
    if zoom != 1:
        img = img.resize((round(img.width * zoom), round(img.height * zoom)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=3)
    return buf.getvalue()


class Hands:
    """Where a click really lands. `tremor` is the standard deviation of the miss in pixels."""

    def __init__(self, tremor=0.0, seed=None):
        self.tremor = tremor
        self.rng = random.Random(seed)

    def aim(self, action):
        if not self.tremor or "x" not in action or action["type"] not in (
                "click", "double_click", "move"):
            return action
        dx, dy = (round(self.rng.gauss(0, self.tremor)) for _ in range(2))
        return {**action, "x": action["x"] + dx, "y": action["y"] + dy}
